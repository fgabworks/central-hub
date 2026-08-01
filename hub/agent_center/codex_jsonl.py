"""Parse Codex `exec --json` JSONL events into hub run fields."""

from __future__ import annotations

import json
from typing import Any

from hub.agent_center.redact import classify_provider_error, redact_text


def parse_jsonl_line(line: str) -> dict[str, Any] | None:
    text = (line or "").strip()
    if not text or not text.startswith("{"):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


class CodexJsonlAccumulator:
    """Incremental Codex JSONL → messages, tools, usage, errors, final answer."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.tool_activity: list[dict[str, Any]] = []
        self.usage: dict[str, Any] = {}
        self.errors: list[str] = []
        self.raw_lines: list[str] = []
        self._final: str = ""

    def feed(self, line: str) -> str | None:
        """Consume one stdout line. Returns a redacted log chunk for the UI stream."""
        self.raw_lines.append(line)
        event = parse_jsonl_line(line)
        if event is None:
            text = line.rstrip("\n")
            return redact_text(text) + ("\n" if line.endswith("\n") else "") if text else None

        etype = str(event.get("type") or "")
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        item_type = str(item.get("type") or "")

        if etype in {"error", "turn.failed"}:
            message = _event_error_message(event)
            if message:
                self.errors.append(message)
            return f"[error] {redact_text(message)}\n"

        if etype == "turn.completed":
            usage = event.get("usage") or event.get("token_usage") or {}
            if isinstance(usage, dict) and usage:
                self.usage = _normalize_usage(usage)
            return "[turn.completed]\n"

        if etype.endswith(".completed") or etype in {"item.completed", "item.updated"}:
            return self._consume_item(item, completed=True)

        if etype.endswith(".started") or etype == "item.started":
            if item_type:
                return self._consume_item(item, completed=False)
            return f"[{etype}]\n"

        if etype:
            return f"[{etype}]\n"
        return None

    def _consume_item(self, item: dict[str, Any], *, completed: bool) -> str:
        item_type = str(item.get("type") or "item")
        if item_type in {"agent_message", "message", "assistant_message"}:
            text = str(item.get("text") or item.get("content") or "").strip()
            if text and completed:
                self.messages.append(text)
                self._final = text
            preview = redact_text(text, limit=400)
            return f"[message] {preview}\n" if preview else f"[{item_type}]\n"

        if item_type in {"reasoning", "thought"}:
            text = str(item.get("text") or item.get("content") or "").strip()
            return f"[reasoning] {redact_text(text, limit=240)}\n" if text else "[reasoning]\n"

        if item_type in {"command_execution", "command", "shell_command"}:
            command = str(item.get("command") or item.get("cmd") or item.get("name") or "").strip()
            status = str(item.get("status") or ("completed" if completed else "started"))
            entry = {
                "type": "command_execution",
                "name": redact_text(command, limit=240) or "command",
                "status": status,
                "detail": redact_text(str(item.get("aggregated_output") or item.get("output") or ""), limit=500),
            }
            if completed or not any(row.get("name") == entry["name"] and row.get("status") == "started" for row in self.tool_activity):
                self.tool_activity.append(entry)
            return f"[tool] {entry['name']} ({status})\n"

        if item_type in {"file_change", "file_changes", "patch"}:
            paths = item.get("paths") or item.get("files") or []
            label = ", ".join(str(p) for p in paths[:5]) if isinstance(paths, list) else str(item.get("path") or "")
            entry = {
                "type": "file_change",
                "name": redact_text(label, limit=240) or "file_change",
                "status": "completed" if completed else "started",
                "detail": "",
            }
            self.tool_activity.append(entry)
            return f"[file] {entry['name']}\n"

        if item_type in {"mcp_tool_call", "web_search", "tool_call"}:
            name = str(item.get("name") or item.get("tool") or item_type)
            entry = {
                "type": item_type,
                "name": redact_text(name, limit=160),
                "status": "completed" if completed else "started",
                "detail": "",
            }
            self.tool_activity.append(entry)
            return f"[tool] {entry['name']} ({entry['status']})\n"

        return f"[{item_type}]\n"

    def final_answer(self) -> str:
        if self._final:
            return redact_text(self._final)
        if self.messages:
            return redact_text(self.messages[-1])
        return ""

    def error_summary(self) -> str:
        if not self.errors:
            return ""
        return classify_provider_error(self.errors[-1])["detail"]


def _event_error_message(event: dict[str, Any]) -> str:
    err = event.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err.get("detail") or err)
    if err:
        return str(err)
    return str(event.get("message") or event.get("detail") or "Codex error")


def _normalize_usage(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "input_tokens": int(raw.get("input_tokens") or raw.get("input") or 0),
        "output_tokens": int(raw.get("output_tokens") or raw.get("output") or 0),
        "total_tokens": int(
            raw.get("total_tokens")
            or (
                int(raw.get("input_tokens") or raw.get("input") or 0)
                + int(raw.get("output_tokens") or raw.get("output") or 0)
            )
        ),
    }
