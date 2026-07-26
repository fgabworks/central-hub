"""Shared Calendar Center service (one implementation for Personal and Work)."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from hub.calendar.api import (
    CalendarApiError,
    CalendarClient,
    parse_calendar_summary,
    parse_event,
)
from hub.calendar.models import (
    DEFAULT_PAGE_SIZE,
    FORBIDDEN_CALENDAR_ACTIONS,
    MAX_PAGE_SIZE,
    UPCOMING_LIMIT,
    normalize_calendar_view,
    normalize_workspace,
)
from hub.email.models import (
    CALENDAR_SCOPES,
    GMAIL_SCOPES,
    has_calendar_scopes,
)
from hub.email.service import EmailService, EmailServiceError
from hub.email.store import EmailStore

HttpGet = Callable[..., Any]
HttpPost = Callable[..., Any]


class CalendarServiceError(Exception):
    def __init__(self, message: str, *, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


class CalendarService:
    """Single Calendar service used by Personal and Work Calendar routes."""

    def __init__(
        self,
        store: EmailStore,
        *,
        email_service: EmailService,
        calendar_client: CalendarClient | None = None,
        http_get: HttpGet | None = None,
    ) -> None:
        self.store = store
        self.email = email_service
        self.client = calendar_client or CalendarClient(http_get=http_get)

    def assert_not_write_action(self, action: str) -> None:
        if (action or "").strip().lower() in FORBIDDEN_CALENDAR_ACTIONS:
            raise CalendarServiceError(
                f"Calendar write action '{action}' is not allowed (readonly mode)",
                code="forbidden",
            )

    def list_accounts(self, workspace: str) -> list[dict[str, Any]]:
        return self.store.list_accounts(workspace=normalize_workspace(workspace))

    def start_calendar_oauth(
        self,
        *,
        workspace: str,
        account_id: str | None = None,
    ) -> dict[str, str]:
        """Incremental OAuth for Calendar — always re-request Gmail+Calendar together.

        Requesting Calendar alone can yield a token that drops Gmail access and looks
        like the account "disconnected" from Email Center.
        """
        scopes = tuple(dict.fromkeys([*GMAIL_SCOPES, *CALENDAR_SCOPES]))
        return self.email.start_oauth(
            workspace=workspace,
            account_id=account_id,
            scopes=scopes,
        )

    def list_calendars(
        self, account_id: str, *, force_refresh: bool = False
    ) -> dict[str, Any]:
        acct = self._require_calendar_account(account_id)
        cache_key = f"cals:{account_id}"
        if not force_refresh:
            cached = self.store.get_calendar_list_cache(cache_key)
            if cached is not None:
                return {**cached, "account": acct, "from_cache": True}
        access = self._access_token(account_id)
        try:
            raw = self.client.list_calendars(access)
        except CalendarApiError as exc:
            self._handle_api_error(account_id, exc)
            raise CalendarServiceError(str(exc), code="calendar_api") from exc
        calendars = [parse_calendar_summary(item) for item in raw]
        payload = {"ok": True, "calendars": calendars, "from_cache": False}
        self.store.put_calendar_list_cache(cache_key, account_id, payload)
        self.store.touch_sync(account_id)
        return {**payload, "account": acct}

    def list_events(
        self,
        account_id: str,
        *,
        view: str = "month",
        calendar_id: str = "",
        q: str = "",
        date_from: str = "",
        date_to: str = "",
        page_token: str | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
        time_zone: str = "",
        force_refresh: bool = False,
        anchor: str | None = None,
    ) -> dict[str, Any]:
        acct = self._require_calendar_account(account_id)
        view_n = normalize_calendar_view(view)
        tz_name = (time_zone or "").strip() or "UTC"
        try:
            tz = ZoneInfo(tz_name)
        except Exception:  # noqa: BLE001
            tz = ZoneInfo("UTC")
            tz_name = "UTC"

        time_min, time_max, anchor_date = _range_for_view(
            view_n,
            anchor=anchor,
            date_from=date_from,
            date_to=date_to,
            tz=tz,
        )
        size = max(1, min(int(page_size), MAX_PAGE_SIZE))
        calendars_payload = self.list_calendars(account_id, force_refresh=force_refresh)
        calendars = calendars_payload["calendars"]
        selected = [c for c in calendars if not calendar_id or c["id"] == calendar_id]
        if calendar_id and not selected:
            raise CalendarServiceError("Calendar not found on this account", code="not_found")
        if not selected:
            selected = calendars

        cache_key = _events_cache_key(
            account_id, view_n, calendar_id, q, time_min, time_max, page_token or "", size, tz_name
        )
        if not force_refresh and not page_token:
            cached = self.store.get_calendar_list_cache(cache_key)
            if cached is not None:
                return {**cached, "account": acct, "from_cache": True}

        access = self._access_token(account_id)
        events: list[dict[str, Any]] = []
        next_token = ""
        # For multi-calendar views without a specific calendar, fetch primary first then others.
        targets = selected
        if not calendar_id and view_n in {"upcoming", "agenda"}:
            primary = next((c for c in selected if c.get("primary")), None)
            targets = [primary] if primary else selected[:1]
            # Also merge selected calendars for upcoming
            if view_n == "upcoming":
                targets = selected

        for cal in targets:
            try:
                raw = self.client.list_events(
                    access,
                    cal["id"],
                    time_min=time_min,
                    time_max=time_max,
                    query=q,
                    page_token=page_token if calendar_id else None,
                    max_results=size,
                    time_zone=tz_name,
                )
            except CalendarApiError as exc:
                self._handle_api_error(account_id, exc)
                raise CalendarServiceError(str(exc), code="calendar_api") from exc
            if calendar_id:
                next_token = str(raw.get("nextPageToken") or "")
            for item in raw.get("items") or []:
                if isinstance(item, dict):
                    parsed = parse_event(item, calendar=cal)
                    events.append(parsed)
                    self.store.put_calendar_event_cache(account_id, cal["id"], parsed)

        events.sort(key=lambda e: _event_sort_key(e))
        if view_n == "upcoming":
            events = events[: max(1, min(size, UPCOMING_LIMIT * 3))][:size]

        result = {
            "ok": True,
            "view": view_n,
            "q": q,
            "calendar_id": calendar_id,
            "time_zone": tz_name,
            "time_min": time_min,
            "time_max": time_max,
            "anchor": anchor_date.isoformat(),
            "calendars": calendars,
            "events": events,
            "next_page_token": next_token,
            "from_cache": False,
        }
        if not page_token:
            self.store.put_calendar_list_cache(
                cache_key, account_id, {k: v for k, v in result.items() if k != "account"}
            )
        self.store.touch_sync(account_id)
        result["account"] = acct
        return result

    def list_events_for_grid(
        self,
        account_id: str,
        *,
        date_from: str,
        date_to: str,
        calendar_id: str = "",
        q: str = "",
        time_zone: str = "",
        force_refresh: bool = False,
        max_pages: int = 5,
    ) -> dict[str, Any]:
        """Fetch events across a date range for the calendar grid (paginated)."""
        from hub.calendar.fc_events import calendar_color_map, to_fullcalendar_event

        collected: list[dict[str, Any]] = []
        page_token: str | None = None
        calendars: list[dict[str, Any]] = []
        time_min = ""
        time_max = ""
        tz_name = (time_zone or "").strip() or "UTC"
        from_cache = False
        for _ in range(max(1, min(int(max_pages), 10))):
            listing = self.list_events(
                account_id,
                view="month",
                calendar_id=calendar_id,
                q=q,
                date_from=date_from,
                date_to=date_to,
                page_token=page_token,
                page_size=MAX_PAGE_SIZE,
                time_zone=tz_name,
                force_refresh=force_refresh and page_token is None,
            )
            calendars = listing.get("calendars") or calendars
            time_min = listing.get("time_min") or time_min
            time_max = listing.get("time_max") or time_max
            tz_name = listing.get("time_zone") or tz_name
            from_cache = bool(listing.get("from_cache")) and page_token is None
            collected.extend(listing.get("events") or [])
            page_token = listing.get("next_page_token") or None
            # Multi-calendar fetches already pull one page per calendar.
            if not calendar_id or not page_token:
                break
        # Deduplicate by calendar_id + event id
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for ev in collected:
            key = f"{ev.get('calendar_id')}:{ev.get('id')}"
            if key in seen:
                continue
            seen.add(key)
            unique.append(ev)
        unique.sort(key=_event_sort_key)
        colors = calendar_color_map(calendars)
        fc_events = [to_fullcalendar_event(ev, calendar_colors=colors) for ev in unique]
        acct = self._require_calendar_account(account_id)
        return {
            "ok": True,
            "account": acct,
            "calendars": calendars,
            "events": unique,
            "fc_events": fc_events,
            "time_min": time_min,
            "time_max": time_max,
            "time_zone": tz_name,
            "from_cache": from_cache,
            "q": q,
            "calendar_id": calendar_id,
        }

    def get_event(
        self,
        account_id: str,
        calendar_id: str,
        event_id: str,
        *,
        force_refresh: bool = False,
        time_zone: str = "",
    ) -> dict[str, Any]:
        acct = self._require_calendar_account(account_id)
        if not force_refresh:
            cached = self.store.get_calendar_event_cache(account_id, calendar_id, event_id)
            if cached is not None:
                return {"ok": True, "account": acct, "event": cached, "from_cache": True}
        access = self._access_token(account_id)
        calendars = self.list_calendars(account_id).get("calendars") or []
        cal = next((c for c in calendars if c["id"] == calendar_id), {"id": calendar_id, "summary": calendar_id})
        try:
            raw = self.client.get_event(
                access, calendar_id, event_id, time_zone=(time_zone or None)
            )
        except CalendarApiError as exc:
            self._handle_api_error(account_id, exc)
            raise CalendarServiceError(str(exc), code="calendar_api") from exc
        event = parse_event(raw, calendar=cal)
        self.store.put_calendar_event_cache(account_id, calendar_id, event)
        return {"ok": True, "account": acct, "event": event, "from_cache": False}

    def upcoming_for_workspace(
        self, workspace: str, *, limit: int = UPCOMING_LIMIT
    ) -> list[dict[str, Any]]:
        """Upcoming events across calendar-enabled accounts in a workspace (best-effort)."""
        ws = normalize_workspace(workspace)
        out: list[dict[str, Any]] = []
        for acct in self.list_accounts(ws):
            if not acct.get("has_calendar") or acct.get("status") != "connected":
                continue
            try:
                listing = self.list_events(
                    acct["id"],
                    view="upcoming",
                    page_size=limit,
                )
            except CalendarServiceError:
                continue
            for event in listing.get("events") or []:
                out.append({**event, "account_email": acct.get("email"), "account_id": acct["id"]})
        out.sort(key=_event_sort_key)
        return out[:limit]

    def refresh_cache(self, account_id: str) -> None:
        self._require_calendar_account(account_id)
        self.store.invalidate_calendar_cache(account_id)

    def _require_calendar_account(self, account_id: str) -> dict[str, Any]:
        try:
            acct = self.email._require_usable_account(account_id)  # noqa: SLF001
        except EmailServiceError as exc:
            raise CalendarServiceError(str(exc), code=exc.code) from exc
        if acct.get("workspace"):
            # ensure public flags
            scopes = acct.get("scopes") or ""
            if not has_calendar_scopes(scopes) and not acct.get("has_calendar"):
                # re-fetch with flags
                acct = self.store.get_account(account_id) or acct
        if not acct.get("has_calendar"):
            raise CalendarServiceError(
                "Calendar scopes not granted — enable Calendar on Google Connections",
                code="missing_scopes",
            )
        return acct

    def _access_token(self, account_id: str) -> str:
        try:
            return self.email._access_token(account_id)  # noqa: SLF001
        except EmailServiceError as exc:
            raise CalendarServiceError(str(exc), code=exc.code) from exc

    def _handle_api_error(self, account_id: str, exc: CalendarApiError) -> None:
        if exc.status_code in {401, 403}:
            self.store.set_account_status(
                account_id,
                "needs_reauth",
                last_error=str(exc)[:300],
            )
        elif exc.rate_limited:
            self.store.set_account_status(account_id, "error", last_error="Rate limited")


def _range_for_view(
    view: str,
    *,
    anchor: str | None,
    date_from: str,
    date_to: str,
    tz: ZoneInfo,
) -> tuple[str, str, date]:
    now = datetime.now(tz)
    if date_from.strip() and date_to.strip():
        start_d = _parse_date(date_from, default=now.date())
        end_d = _parse_date(date_to, default=start_d + timedelta(days=7))
        return _day_start_iso(start_d, tz), _day_end_iso(end_d, tz), start_d

    if anchor:
        base = _parse_date(anchor, default=now.date())
    else:
        base = now.date()

    if view == "day":
        return _day_start_iso(base, tz), _day_end_iso(base, tz), base
    if view == "week":
        start = base - timedelta(days=base.weekday())
        end = start + timedelta(days=6)
        return _day_start_iso(start, tz), _day_end_iso(end, tz), start
    if view == "month":
        start = base.replace(day=1)
        if start.month == 12:
            nxt = start.replace(year=start.year + 1, month=1, day=1)
        else:
            nxt = start.replace(month=start.month + 1, day=1)
        end = nxt - timedelta(days=1)
        return _day_start_iso(start, tz), _day_end_iso(end, tz), start
    if view == "agenda":
        end = base + timedelta(days=30)
        return _day_start_iso(base, tz), _day_end_iso(end, tz), base
    # upcoming
    end = base + timedelta(days=14)
    start_dt = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return start_dt, _day_end_iso(end, tz), base


def _parse_date(value: str, *, default: date) -> date:
    try:
        return date.fromisoformat((value or "").strip()[:10])
    except ValueError:
        return default


def _day_start_iso(d: date, tz: ZoneInfo) -> str:
    dt = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=tz).astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _day_end_iso(d: date, tz: ZoneInfo) -> str:
    dt = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=tz).astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _event_sort_key(event: dict[str, Any]) -> str:
    start = event.get("start") or {}
    return str(start.get("date_time") or start.get("date") or "")


def _events_cache_key(
    account_id: str,
    view: str,
    calendar_id: str,
    q: str,
    time_min: str,
    time_max: str,
    page_token: str,
    size: int,
    tz: str,
) -> str:
    raw = f"{account_id}|{view}|{calendar_id}|{q}|{time_min}|{time_max}|{page_token}|{size}|{tz}"
    return "ev:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
