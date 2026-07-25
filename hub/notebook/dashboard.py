"""Dashboard work-queue helpers over the Repository Notebook store."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Iterable

from hub.notebook.models import (
    NOTE_TYPE_LABELS,
    PRIORITY_LABELS,
    REPO_ROLE_LABELS,
    STATUS_LABELS,
    normalize_role,
)
from hub.notebook.store import NotebookStore

QUEUE_TABS = ("open", "pinned", "overdue", "due_today", "upcoming", "blocked")


def _today() -> date:
    return date.today()


def _parse_due(value: str | None) -> date | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _format_due(due: date | None, *, today: date) -> dict[str, Any]:
    if due is None:
        return {"label": "—", "kind": "none", "is_overdue": False}
    if due < today:
        return {"label": "Overdue", "kind": "overdue", "is_overdue": True}
    if due == today:
        return {"label": "Today", "kind": "today", "is_overdue": False}
    return {
        "label": due.strftime("%b %d").replace(" 0", " "),
        "kind": "upcoming",
        "is_overdue": False,
    }


def build_repo_summary(
    note: dict[str, Any],
    registered_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """
    Build Work Queue repository cell data from note_repositories associations.

    Primary = role primary, else first linked repo. Extra links become +N.
    Missing registry ids surface as Unavailable (stored label preserved).
    """
    registered = {str(x) for x in (registered_ids or []) if str(x).strip()}
    raw_repos = list(note.get("repositories") or [])

    def sort_key(item: dict[str, Any]) -> tuple:
        role = normalize_role(item.get("role"))
        try:
            order = int(item.get("sort_order") or 0)
        except (TypeError, ValueError):
            order = 0
        return (0 if role == "primary" else 1, order, str(item.get("repository_id") or ""))

    ordered = sorted(raw_repos, key=sort_key)
    details: list[dict[str, Any]] = []
    tooltip_lines: list[str] = []
    for item in ordered:
        repo_id = str(item.get("repository_id") or "").strip()
        label = str(item.get("repository_label") or repo_id or "").strip() or "—"
        role = normalize_role(item.get("role"))
        role_label = REPO_ROLE_LABELS.get(role, role)
        available = bool(repo_id) and repo_id in registered
        if available:
            display = label
        elif label and label != "—":
            display = f"{label} (Unavailable)"
        else:
            display = "Unavailable"
        details.append(
            {
                "repository_id": repo_id,
                "label": label,
                "role": role,
                "role_label": role_label,
                "available": available,
                "display": display,
            }
        )
        tooltip_lines.append(f"{role_label}: {display}")

    primary = next((d for d in details if d["role"] == "primary"), None)
    if primary is None and details:
        primary = details[0]

    extra = max(0, len(details) - 1) if details else 0
    if primary is None:
        cell = "—"
        primary_name = "—"
        primary_unavailable = False
    else:
        primary_name = primary["display"]
        primary_unavailable = not primary["available"]
        cell = f"{primary_name} +{extra}" if extra else primary_name

    return {
        "repo_cell": cell,
        "repo_primary_name": primary_name,
        "repo_extra_count": extra,
        "repo_primary_unavailable": primary_unavailable,
        "repo_tooltip": "\n".join(tooltip_lines) if tooltip_lines else "No linked repositories",
        "repo_details": details,
        # Keep primary_repository aligned with role-aware primary for templates.
        "primary_repository": primary_name if primary is not None else "—",
    }


def classify_open_note(
    note: dict[str, Any],
    *,
    today: date | None = None,
    registered_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Attach due/tab flags and repository display used by the dashboard work queue."""
    day = today or _today()
    due = _parse_due(note.get("due_date"))
    due_meta = _format_due(due, today=day)
    status = str(note.get("status") or "")
    pinned = bool(note.get("pinned"))
    week_end = day + timedelta(days=7)
    flags = {
        "open": True,
        "pinned": pinned,
        "overdue": bool(due and due < day),
        "due_today": bool(due and due == day),
        "upcoming": bool(due and due > day),
        "due_this_week": bool(due and day <= due <= week_end),
        "blocked": status == "blocked",
    }
    return {
        **note,
        **build_repo_summary(note, registered_ids),
        "due_meta": due_meta,
        "flags": flags,
        "status_label": STATUS_LABELS.get(status, status),
        "priority_label": PRIORITY_LABELS.get(str(note.get("priority") or ""), note.get("priority")),
        "type_label": NOTE_TYPE_LABELS.get(str(note.get("note_type") or ""), note.get("note_type")),
    }


def open_task_stats(
    notes: list[dict[str, Any]],
    *,
    today: date | None = None,
    registered_ids: Iterable[str] | None = None,
) -> dict[str, int]:
    day = today or _today()
    classified = [
        classify_open_note(n, today=day, registered_ids=registered_ids) for n in notes
    ]
    return {
        "open": len(classified),
        "overdue": sum(1 for n in classified if n["flags"]["overdue"]),
        "due_this_week": sum(1 for n in classified if n["flags"]["due_this_week"]),
        "blocked": sum(1 for n in classified if n["flags"]["blocked"]),
        "pinned": sum(1 for n in classified if n["flags"]["pinned"]),
        "due_today": sum(1 for n in classified if n["flags"]["due_today"]),
        "upcoming": sum(1 for n in classified if n["flags"]["upcoming"]),
    }


def open_tasks_severity(stats: dict[str, int] | None) -> str:
    """
    Visual severity for the Open Tasks summary card.

    - neutral: no open tasks
    - attention: open tasks, none overdue/blocked (subtle crimson)
    - alert: overdue or blocked present (stronger red glow)
    """
    data = stats or {}
    open_n = int(data.get("open") or 0)
    if open_n <= 0:
        return "neutral"
    if int(data.get("overdue") or 0) > 0 or int(data.get("blocked") or 0) > 0:
        return "alert"
    return "attention"


def _normalize_tab(tab: str | None) -> str:
    key = (tab or "open").strip().lower()
    return key if key in QUEUE_TABS else "open"


def _status_sort_rank(status: str | None) -> int:
    """Pending first, Done last; other open statuses in between."""
    order = {
        "pending": 0,
        "inbox": 1,
        "ongoing": 2,
        "blocked": 3,
        "done": 4,
        "archived": 5,
    }
    return order.get(str(status or "").strip().lower(), 3)


def filter_queue(
    notes: list[dict[str, Any]],
    tab: str,
    *,
    today: date | None = None,
    limit: int = 5,
    registered_ids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    day = today or _today()
    key = _normalize_tab(tab)
    classified = [
        classify_open_note(n, today=day, registered_ids=registered_ids) for n in notes
    ]

    def matches(item: dict[str, Any]) -> bool:
        flags = item["flags"]
        if key == "open":
            return True
        if key == "pinned":
            return flags["pinned"]
        if key == "overdue":
            return flags["overdue"]
        if key == "due_today":
            return flags["due_today"]
        if key == "upcoming":
            return flags["upcoming"]
        if key == "blocked":
            return flags["blocked"]
        return False

    matched = [n for n in classified if matches(n)]
    # Newest first, then stable-sort: Pending → … → Done, then pin / due.
    matched.sort(key=lambda n: str(n.get("updated_at") or ""), reverse=True)
    matched.sort(
        key=lambda item: (
            _status_sort_rank(item.get("status")),
            0 if item["flags"]["pinned"] else 1,
            0 if item["flags"]["overdue"] else 1,
            0 if item["flags"]["due_today"] else 1,
            0 if _parse_due(item.get("due_date")) is not None else 1,
            _parse_due(item.get("due_date")) or date.max,
        )
    )
    return matched[: max(0, min(int(limit), 20))]


def dashboard_work_queue(
    store: NotebookStore,
    *,
    tab: str = "open",
    limit: int = 5,
    today: date | None = None,
    registered_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    day = today or _today()
    open_notes = store.list_open(limit=500)
    stats = open_task_stats(open_notes, today=day, registered_ids=registered_ids)
    active = _normalize_tab(tab)
    items = filter_queue(
        open_notes,
        active,
        today=day,
        limit=limit,
        registered_ids=registered_ids,
    )
    return {
        "tab": active,
        "tabs": {
            "open": stats["open"],
            "pinned": stats["pinned"],
            "overdue": stats["overdue"],
            "due_today": stats["due_today"],
            "upcoming": stats["upcoming"],
            "blocked": stats["blocked"],
        },
        "stats": stats,
        "notes": items,
        "total_open": stats["open"],
        "shown": len(items),
    }
