"""TODAY Mission Control — daily missions stored as Work Notebook notes."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from hub.notebook.db import utcnow
from hub.notebook.models import (
    MISSION_REMINDER_BEFORE_HOUR,
    PRIORITY_LABELS,
    normalize_priority,
    normalize_reminder_status,
    normalize_scope,
)
from hub.notebook.store import NotebookStore

MISSION_NOTE_TYPE = "mission"
MISSION_SCOPE = "work"
OPEN_STATUSES = frozenset({"inbox", "pending", "ongoing", "blocked"})
WIDGET_TOP_LIMIT = 5


def _local_now(now: datetime | None = None) -> datetime:
    if now is not None:
        if now.tzinfo is not None:
            return now.astimezone().replace(tzinfo=None)
        return now
    return datetime.now()


def _today(now: datetime | None = None) -> date:
    return _local_now(now).date()


def _parse_day(value: str | None) -> date | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _completed_day(value: str | None) -> date | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        if "T" in raw:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _format_completed(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    try:
        if "T" in raw:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                dt = dt.astimezone().replace(tzinfo=None)
            return dt.strftime("%H:%M")
        return raw[:16]
    except ValueError:
        return raw[:16]


def _is_open(note: dict[str, Any]) -> bool:
    return str(note.get("status") or "") in OPEN_STATUSES


def _priority_rank(priority: str | None) -> int:
    raw = normalize_priority(priority)
    if raw == "urgent":
        return 0
    if raw == "high":
        return 1
    if raw == "medium":
        return 2
    return 3


def _is_high_priority(priority: str | None) -> bool:
    return normalize_priority(priority) in {"urgent", "high"}


def _hydrate_mission_flags(note: dict[str, Any]) -> dict[str, Any]:
    out = dict(note)
    out["carry_over"] = bool(int(out.get("carry_over") or 0))
    out["reminder_status"] = normalize_reminder_status(out.get("reminder_status"))
    out["priority"] = normalize_priority(out.get("priority"))
    out["priority_label"] = PRIORITY_LABELS.get(out["priority"], out["priority"])
    out["completed_time_label"] = _format_completed(out.get("completed_at"))
    out["original_due_date"] = (out.get("original_due_date") or out.get("due_date") or "") or ""
    out["is_overdue_carry"] = bool(out["carry_over"] and _is_open(out))
    out["show_priority_badge"] = _is_high_priority(out["priority"]) and _is_open(out)
    return out


def ordered_widget_missions(
    *,
    carry_over: list[dict[str, Any]],
    today_open: list[dict[str, Any]],
    completed_today: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Dashboard list order: carry-over → high priority → other open → completed."""
    high = [m for m in today_open if _is_high_priority(m.get("priority"))]
    other = [m for m in today_open if not _is_high_priority(m.get("priority"))]
    high.sort(key=lambda m: (_priority_rank(m.get("priority")), str(m.get("created_at") or "")))
    other.sort(key=lambda m: (_priority_rank(m.get("priority")), str(m.get("created_at") or "")))
    completed = sorted(
        completed_today,
        key=lambda m: str(m.get("completed_at") or m.get("updated_at") or ""),
        reverse=True,
    )
    return list(carry_over) + high + other + completed


class MissionControl:
    """Daily execution board backed by Work Notebook mission notes."""

    def __init__(self, store: NotebookStore) -> None:
        self.store = store

    def list_missions(self, *, include_done: bool = True) -> list[dict[str, Any]]:
        clauses = ["n.scope = ?", "n.note_type = ?"]
        params: list[Any] = [MISSION_SCOPE, MISSION_NOTE_TYPE]
        if not include_done:
            clauses.append("n.status NOT IN ('done', 'archived')")
        else:
            clauses.append("n.status != 'archived'")
        sql = f"""
            SELECT n.*
            FROM notes n
            WHERE {" AND ".join(clauses)}
            ORDER BY
                CASE n.priority
                    WHEN 'urgent' THEN 0
                    WHEN 'high' THEN 1
                    WHEN 'medium' THEN 2
                    ELSE 3
                END,
                n.due_date IS NULL,
                n.due_date ASC,
                n.created_at ASC
        """
        with self.store.db.connect() as conn:
            rows = [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
            notes = self.store._hydrate_notes_batch(rows, conn, light=True)
        return [_hydrate_mission_flags(n) for n in notes]

    def create_mission(
        self,
        *,
        title: str,
        body_md: str = "",
        priority: str = "medium",
        due_date: str | None = None,
        actor: str = "owner",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        today = _today(now)
        target = (due_date or "").strip() or today.isoformat()
        if _parse_day(target) is None:
            target = today.isoformat()
        note = self.store.create(
            title=(title or "").strip() or "Untitled mission",
            actor=actor,
            scope=MISSION_SCOPE,
            note_type=MISSION_NOTE_TYPE,
        )
        stamp = utcnow()
        with self.store.db.connect() as conn:
            conn.execute(
                """
                UPDATE notes SET
                    body_md = ?,
                    status = 'pending',
                    priority = ?,
                    due_date = ?,
                    original_due_date = ?,
                    completed_at = NULL,
                    reminder_status = 'none',
                    carry_over = 0,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    body_md or "",
                    normalize_priority(priority),
                    target,
                    target,
                    stamp,
                    note["id"],
                ),
            )
            self.store._add_activity(
                conn,
                note["id"],
                action="mission_created",
                detail=f"Mission created for {target}",
                actor=actor,
                when=stamp,
            )
        return _hydrate_mission_flags(self.store.get(note["id"]) or note)

    def complete_mission(
        self,
        note_id: str,
        *,
        actor: str = "owner",
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        note = self.store.get(note_id)
        if not note or not self._is_mission(note):
            return None
        stamp = utcnow()
        with self.store.db.connect() as conn:
            conn.execute(
                """
                UPDATE notes SET
                    status = 'done',
                    completed_at = ?,
                    updated_at = ?,
                    reminder_status = CASE
                        WHEN reminder_status = 'pending' THEN 'skipped'
                        ELSE reminder_status
                    END
                WHERE id = ?
                """,
                (stamp, stamp, note_id),
            )
            self.store._add_activity(
                conn,
                note_id,
                action="mission_completed",
                detail="Mission marked complete",
                actor=actor,
                when=stamp,
            )
        return _hydrate_mission_flags(self.store.get(note_id) or {})

    def reopen_mission(
        self,
        note_id: str,
        *,
        actor: str = "owner",
    ) -> dict[str, Any] | None:
        note = self.store.get(note_id)
        if not note or not self._is_mission(note):
            return None
        stamp = utcnow()
        with self.store.db.connect() as conn:
            conn.execute(
                """
                UPDATE notes SET
                    status = 'pending',
                    completed_at = NULL,
                    updated_at = ?,
                    reminder_status = 'none'
                WHERE id = ?
                """,
                (stamp, note_id),
            )
            self.store._add_activity(
                conn,
                note_id,
                action="mission_reopened",
                detail="Mission reopened",
                actor=actor,
                when=stamp,
            )
        return _hydrate_mission_flags(self.store.get(note_id) or {})

    def reschedule_mission(
        self,
        note_id: str,
        *,
        due_date: str,
        actor: str = "owner",
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        note = self.store.get(note_id)
        if not note or not self._is_mission(note):
            return None
        today = _today(now)
        target = (due_date or "").strip()
        if _parse_day(target) is None:
            target = today.isoformat()
        original = (note.get("original_due_date") or note.get("due_date") or target).strip()
        stamp = utcnow()
        with self.store.db.connect() as conn:
            conn.execute(
                """
                UPDATE notes SET
                    due_date = ?,
                    original_due_date = ?,
                    carry_over = 0,
                    reminder_status = 'none',
                    status = CASE WHEN status = 'done' THEN 'pending' ELSE status END,
                    completed_at = CASE WHEN status = 'done' THEN NULL ELSE completed_at END,
                    updated_at = ?
                WHERE id = ?
                """,
                (target, original or target, stamp, note_id),
            )
            self.store._add_activity(
                conn,
                note_id,
                action="mission_rescheduled",
                detail=f"Mission rescheduled to {target}",
                actor=actor,
                when=stamp,
            )
        return _hydrate_mission_flags(self.store.get(note_id) or {})

    def process_carry_over(
        self,
        *,
        now: datetime | None = None,
        actor: str = "system",
    ) -> list[dict[str, Any]]:
        """Mark unfinished missions past their target day as carry-over."""
        today = _today(now)
        changed: list[dict[str, Any]] = []
        for note in self.list_missions(include_done=False):
            due = _parse_day(note.get("due_date"))
            if due is None or due >= today:
                continue
            if note.get("carry_over"):
                continue
            original = (note.get("original_due_date") or note.get("due_date") or due.isoformat()).strip()
            stamp = utcnow()
            with self.store.db.connect() as conn:
                conn.execute(
                    """
                    UPDATE notes SET
                        carry_over = 1,
                        original_due_date = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (original, stamp, note["id"]),
                )
                self.store._add_activity(
                    conn,
                    note["id"],
                    action="mission_carry_over",
                    detail=f"Carried over from {original}",
                    actor=actor,
                    when=stamp,
                )
            updated = self.store.get(note["id"])
            if updated:
                changed.append(_hydrate_mission_flags(updated))
        return changed

    def process_reminders(
        self,
        *,
        now: datetime | None = None,
        actor: str = "system",
    ) -> list[dict[str, Any]]:
        """
        Before 5 PM local time, mark unfinished TODAY missions as reminded.

        Returns missions whose reminder was newly sent on this pass.
        """
        local = _local_now(now)
        today = local.date()
        if local.time() >= time(MISSION_REMINDER_BEFORE_HOUR, 0):
            return []

        sent: list[dict[str, Any]] = []
        for note in self.list_missions(include_done=False):
            due = _parse_day(note.get("due_date"))
            if due != today:
                continue
            if note.get("carry_over"):
                continue
            status = normalize_reminder_status(note.get("reminder_status"))
            if status == "sent":
                continue
            stamp = utcnow()
            with self.store.db.connect() as conn:
                conn.execute(
                    """
                    UPDATE notes SET reminder_status = 'sent', updated_at = ?
                    WHERE id = ?
                    """,
                    (stamp, note["id"]),
                )
                self.store._add_activity(
                    conn,
                    note["id"],
                    action="mission_reminder",
                    detail="Reminder sent before 5 PM for unfinished TODAY mission",
                    actor=actor,
                    when=stamp,
                )
            updated = self.store.get(note["id"])
            if updated:
                sent.append(_hydrate_mission_flags(updated))
        return sent

    def sync_day(
        self,
        *,
        now: datetime | None = None,
        actor: str = "system",
    ) -> dict[str, Any]:
        carried = self.process_carry_over(now=now, actor=actor)
        reminded = self.process_reminders(now=now, actor=actor)
        return {
            "carried": carried,
            "reminded": reminded,
            "today": _today(now).isoformat(),
        }

    def board(
        self,
        *,
        now: datetime | None = None,
        sync: bool = True,
        actor: str = "system",
    ) -> dict[str, Any]:
        sync_info = self.sync_day(now=now, actor=actor) if sync else {
            "carried": [],
            "reminded": [],
            "today": _today(now).isoformat(),
        }
        today = _today(now)
        today_open: list[dict[str, Any]] = []
        carry_over: list[dict[str, Any]] = []
        completed_today: list[dict[str, Any]] = []

        for note in self.list_missions(include_done=True):
            due = _parse_day(note.get("due_date"))
            completed_on = _completed_day(note.get("completed_at"))
            if note.get("status") == "done":
                if completed_on == today or (due == today and completed_on is None):
                    completed_today.append(note)
                continue
            if not _is_open(note):
                continue
            if note.get("carry_over") or (due is not None and due < today):
                carry_over.append(note)
            elif due == today or due is None:
                # Untargeted open missions created today surface under TODAY.
                created = _parse_day(str(note.get("created_at") or "")[:10])
                if due == today or created == today:
                    today_open.append(note)

        total_today = len(today_open) + len(completed_today)
        done = len(completed_today)
        pending = len(today_open)
        overdue = len(carry_over)
        total_all = pending + overdue + done
        progress_pct = int(round((done / total_today) * 100)) if total_today else 0

        return {
            "today": today.isoformat(),
            "today_label": today.strftime("%a %b %d, %Y").replace(" 0", " "),
            "today_open": today_open,
            "carry_over": carry_over,
            "completed_today": completed_today,
            "progress": {
                "done": done,
                "total": total_today,
                "pending": pending,
                "overdue": overdue,
                "percent": progress_pct,
                "label": f"{done}/{total_today} Completed",
                "total_all": total_all,
            },
            "reminder": {
                "active": bool(sync_info.get("reminded")),
                "count": len(sync_info.get("reminded") or []),
                "missions": sync_info.get("reminded") or [],
                "before_hour": MISSION_REMINDER_BEFORE_HOUR,
            },
            "sync": {
                "carried_count": len(sync_info.get("carried") or []),
                "reminded_count": len(sync_info.get("reminded") or []),
            },
        }

    def widget(
        self,
        *,
        now: datetime | None = None,
        sync: bool = True,
        actor: str = "system",
        top_limit: int = WIDGET_TOP_LIMIT,
    ) -> dict[str, Any]:
        board = self.board(now=now, sync=sync, actor=actor)
        limit = max(1, min(int(top_limit), 20))
        ordered = ordered_widget_missions(
            carry_over=list(board["carry_over"]),
            today_open=list(board["today_open"]),
            completed_today=list(board["completed_today"]),
        )
        preview = ordered[:limit]
        return {
            "today": board["today"],
            "today_label": board["today_label"],
            "progress": board["progress"],
            "reminder": board["reminder"],
            "top_missions": preview,
            "mission_count": len(ordered),
            "has_more": len(ordered) > limit,
            "limit": limit,
            "open_href_view": "missions",
        }

    def clear_missions(
        self,
        *,
        mode: str = "completed",
        actor: str = "owner",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """
        Clear missions without hard-delete.

        mode=completed (default): archive completed-today missions.
        mode=all: archive all non-archived missions (open + completed).
        Activity/history rows remain via note_activity + archived notes.
        """
        mode_n = (mode or "completed").strip().lower()
        if mode_n not in {"completed", "all"}:
            mode_n = "completed"
        board = self.board(now=now, sync=False, actor=actor)
        if mode_n == "completed":
            targets = list(board["completed_today"])
        else:
            targets = (
                list(board["carry_over"])
                + list(board["today_open"])
                + list(board["completed_today"])
            )
        cleared_ids: list[str] = []
        for note in targets:
            note_id = str(note.get("id") or "")
            if not note_id:
                continue
            archived = self.store.archive(note_id, actor=actor)
            if archived:
                cleared_ids.append(note_id)
                stamp = utcnow()
                with self.store.db.connect() as conn:
                    self.store._add_activity(
                        conn,
                        note_id,
                        action="mission_cleared",
                        detail=f"Mission cleared from dashboard ({mode_n})",
                        actor=actor,
                        when=stamp,
                    )
        return {
            "mode": mode_n,
            "cleared_count": len(cleared_ids),
            "cleared_ids": cleared_ids,
            "widget": self.widget(now=now, sync=False, actor=actor),
        }

    @staticmethod
    def _is_mission(note: dict[str, Any]) -> bool:
        return (
            normalize_scope(note.get("scope")) == MISSION_SCOPE
            and str(note.get("note_type") or "") == MISSION_NOTE_TYPE
        )


def mission_control(store: NotebookStore) -> MissionControl:
    return MissionControl(store)
