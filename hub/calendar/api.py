"""Google Calendar REST API client (readonly). Injectable HTTP for tests."""

from __future__ import annotations

from typing import Any, Callable
from urllib.parse import quote

import requests

from hub.calendar.models import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

CALENDAR_API = "https://www.googleapis.com/calendar/v3"

HttpGet = Callable[..., Any]


class CalendarApiError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        rate_limited: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.rate_limited = rate_limited


class CalendarClient:
    def __init__(self, *, http_get: HttpGet | None = None, timeout: float = 25.0) -> None:
        self._get = http_get or requests.get
        self.timeout = timeout

    def list_calendars(self, access_token: str) -> list[dict[str, Any]]:
        data = self._request(access_token, "/users/me/calendarList")
        items = data.get("items") or []
        return [item for item in items if isinstance(item, dict)]

    def list_events(
        self,
        access_token: str,
        calendar_id: str,
        *,
        time_min: str | None = None,
        time_max: str | None = None,
        query: str = "",
        page_token: str | None = None,
        max_results: int = DEFAULT_PAGE_SIZE,
        single_events: bool = True,
        order_by: str = "startTime",
        time_zone: str | None = None,
    ) -> dict[str, Any]:
        cid = quote(calendar_id, safe="")
        params: dict[str, Any] = {
            "maxResults": max(1, min(int(max_results), MAX_PAGE_SIZE)),
            "singleEvents": "true" if single_events else "false",
        }
        if single_events and order_by:
            params["orderBy"] = order_by
        if time_min:
            params["timeMin"] = time_min
        if time_max:
            params["timeMax"] = time_max
        if query.strip():
            params["q"] = query.strip()
        if page_token:
            params["pageToken"] = page_token
        if time_zone:
            params["timeZone"] = time_zone
        return self._request(access_token, f"/calendars/{cid}/events", params=params)

    def get_event(
        self, access_token: str, calendar_id: str, event_id: str, *, time_zone: str | None = None
    ) -> dict[str, Any]:
        cid = quote(calendar_id, safe="")
        eid = quote(event_id, safe="")
        params: dict[str, Any] = {}
        if time_zone:
            params["timeZone"] = time_zone
        return self._request(access_token, f"/calendars/{cid}/events/{eid}", params=params or None)

    def _request(
        self,
        access_token: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not access_token:
            raise CalendarApiError("Missing access token", status_code=401)
        url = f"{CALENDAR_API}{path}"
        try:
            resp = self._get(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                params=params or {},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise CalendarApiError("Calendar API network error") from exc
        try:
            payload = resp.json() if resp.content else {}
        except Exception:  # noqa: BLE001
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if resp.status_code == 429:
            raise CalendarApiError(
                "Calendar API rate limit exceeded — try again shortly",
                status_code=429,
                rate_limited=True,
            )
        if resp.status_code in {401, 403}:
            api_msg = ""
            err = payload.get("error")
            if isinstance(err, dict):
                api_msg = str(err.get("message") or "")
            lowered = api_msg.lower()
            if "has not been used" in lowered or "disabled" in lowered or "accessnotconfigured" in lowered.replace(" ", ""):
                raise CalendarApiError(
                    "Google Calendar API is not enabled for this Cloud project — "
                    "enable it in Google Cloud Console, then reconnect",
                    status_code=resp.status_code,
                )
            if resp.status_code == 401:
                raise CalendarApiError(
                    "Calendar token expired or invalid — reconnect on Google Connections",
                    status_code=401,
                )
            raise CalendarApiError(
                "Calendar access denied — reconnect and approve Calendar scopes "
                "(and enable Google Calendar API in Cloud Console)",
                status_code=resp.status_code,
            )
        if resp.status_code >= 400:
            msg = str(payload.get("error", {}).get("message") or "Calendar API error")
            raise CalendarApiError(msg[:200], status_code=resp.status_code)
        return payload


def parse_calendar_summary(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(raw.get("id") or ""),
        "summary": str(raw.get("summary") or raw.get("id") or "Calendar"),
        "description": str(raw.get("description") or ""),
        "primary": bool(raw.get("primary")),
        "access_role": str(raw.get("accessRole") or ""),
        "background_color": str(raw.get("backgroundColor") or ""),
        "foreground_color": str(raw.get("foregroundColor") or ""),
        "time_zone": str(raw.get("timeZone") or ""),
        "selected": bool(raw.get("selected", True)),
    }


def parse_event(raw: dict[str, Any], *, calendar: dict[str, Any] | None = None) -> dict[str, Any]:
    start = raw.get("start") or {}
    end = raw.get("end") or {}
    start_date = start.get("date")
    end_date = end.get("date")
    all_day = bool(start_date and not start.get("dateTime"))
    hangout = raw.get("hangoutLink") or ""
    conf = raw.get("conferenceData") or {}
    meet = hangout
    for ep in conf.get("entryPoints") or []:
        if isinstance(ep, dict) and ep.get("entryPointType") == "video" and ep.get("uri"):
            meet = str(ep.get("uri"))
            break
    attendees = []
    for att in raw.get("attendees") or []:
        if not isinstance(att, dict):
            continue
        attendees.append(
            {
                "email": str(att.get("email") or ""),
                "display_name": str(att.get("displayName") or ""),
                "response_status": str(att.get("responseStatus") or ""),
                "organizer": bool(att.get("organizer")),
                "self": bool(att.get("self")),
            }
        )
    cal = calendar or {}
    return {
        "id": str(raw.get("id") or ""),
        "calendar_id": str(cal.get("id") or raw.get("organizer", {}).get("email") or ""),
        "calendar_summary": str(cal.get("summary") or ""),
        "summary": str(raw.get("summary") or "(no title)"),
        "description": str(raw.get("description") or ""),
        "location": str(raw.get("location") or ""),
        "status": str(raw.get("status") or ""),
        "html_link": str(raw.get("htmlLink") or ""),
        "hangout_link": meet,
        "all_day": all_day,
        "start": {
            "date": start_date,
            "date_time": start.get("dateTime"),
            "time_zone": start.get("timeZone") or cal.get("time_zone") or "",
        },
        "end": {
            "date": end_date,
            "date_time": end.get("dateTime"),
            "time_zone": end.get("timeZone") or cal.get("time_zone") or "",
        },
        "recurring_event_id": str(raw.get("recurringEventId") or ""),
        "recurrence": list(raw.get("recurrence") or []),
        "attendees": attendees,
        "organizer": {
            "email": str((raw.get("organizer") or {}).get("email") or ""),
            "display_name": str((raw.get("organizer") or {}).get("displayName") or ""),
        },
        "created": str(raw.get("created") or ""),
        "updated": str(raw.get("updated") or ""),
        "i_cal_uid": str(raw.get("iCalUID") or ""),
    }
