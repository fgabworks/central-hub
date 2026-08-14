"""Provider investigation telemetry: inspected files vs search matches vs tool calls."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_PATH_RE = re.compile(
    r"(?:[A-Za-z]:)?[\\/]?(?:[\w.-]+[\\/])+[\w.-]+\.[A-Za-z0-9]+",
)
_SIMPLE_FILE_RE = re.compile(
    r"(^|[\s\"'`=])((?:[\w.-]+[\\/])+[\w.-]+\.[A-Za-z][A-Za-z0-9]{0,11})",
)
_BARE_FILE_RE = re.compile(
    r"(?:^|[\s\"'`=,=(])((?:\.?[\w.-]+[\\/])*[\w.-]+\.[A-Za-z][A-Za-z0-9]{0,11})",
)
_PATH_FLAG_RE = re.compile(
    r"""(?:-(?:Path|LiteralPath)|--path)\s+(?:'(?:\\'|[^'])*'|"(?:\\"|[^"])*"|\S+)""",
    re.I,
)
_READ_RE = re.compile(
    r"(?:^|[^\w-])(?:get-content|gc|cat|sed|less|more|head|tail|nl|bat|type)\b",
    re.I,
)
_READ_TAIL_RE = re.compile(
    r"(?:get-content|gc|cat|sed|less|more|head|tail|nl|bat|type)\b(.*)$",
    re.I | re.S,
)
_SEARCH_RE = re.compile(
    r"(?:^|[^\w-])(?:rg(?:\.exe)?|ripgrep|grep|git\s+grep|findstr|select-string|find)\b",
    re.I,
)
_FAILED_RE = re.compile(
    r"(?:cannot find path|wildcard|parameter cannot be found|is not recognized|"
    r"commandnotfound|exit(?:ed)? with (?:code|status) [1-9]|"
    r"no such file|not a valid|"
    r"the term '.+' is not recognized)",
    re.I,
)
_INVALID_GLOB_RE = re.compile(
    r"(?:(?:^|\s)(?:tests|lookup|src|pkg)(?:\s+\*\.\w+|[^\s]*\*))|"
    r"(?:^|\s)\*\.\w+",
    re.I,
)
_GLOB_FLAG_RE = re.compile(
    r"""(?:--glob|-g|--iglob)\s+(?:'(?:\\'|[^'])*'|"(?:\\"|[^"])*"|\S+)""",
    re.I,
)
_SKIP_PATH_PARTS = ("windows/system32", "program files", "appdata/local/temp")


@dataclass
class InvestigationSummary:
    files_inspected: int | None = None
    inspected_paths: list[str] = field(default_factory=list)
    search_matched_files: int | None = None
    search_matched_paths: list[str] = field(default_factory=list)
    tool_calls: int | None = None
    search_commands: list[dict[str, Any]] = field(default_factory=list)
    successful_searches: int = 0
    failed_searches: int = 0
    invalid_windows_globs: int = 0

    def public(self) -> dict[str, Any]:
        return {
            "files_inspected": self.files_inspected,
            "inspected_paths": list(self.inspected_paths[:24]),
            "search_matched_files": self.search_matched_files,
            "search_matched_paths": list(self.search_matched_paths[:24]),
            "tool_calls": self.tool_calls,
            "successful_searches": self.successful_searches,
            "failed_searches": self.failed_searches,
            "invalid_windows_globs": self.invalid_windows_globs,
            "search_commands": [
                {
                    "command": row.get("command"),
                    "ok": row.get("ok"),
                    "invalid_windows_glob": row.get("invalid_windows_glob"),
                    "matched_files": row.get("matched_files"),
                }
                for row in self.search_commands[:24]
            ],
        }


def summarize_tool_activity(
    tool_activity: list[dict[str, Any]] | None,
    logs: str = "",
) -> InvestigationSummary:
    if tool_activity is None and not str(logs or "").strip():
        return InvestigationSummary()
    inspected: list[str] = []
    matched: list[str] = []
    seen_inspected: set[str] = set()
    seen_matched: set[str] = set()
    commands: list[dict[str, Any]] = []
    tool_calls = 0
    successful = 0
    failed = 0
    invalid = 0

    for item in tool_activity or []:
        if not isinstance(item, dict):
            continue
        tool_calls += 1
        command = str(item.get("name") or item.get("command") or "")
        detail = str(item.get("detail") or item.get("output") or "")
        status = str(item.get("status") or "")
        if is_search_command(command):
            ok = not command_failed(item)
            glob_bad = has_invalid_windows_search_glob(command)
            hits = extract_paths(detail) if ok else []
            commands.append({
                "command": command[:240],
                "ok": ok,
                "invalid_windows_glob": glob_bad,
                "matched_files": len(hits),
            })
            if glob_bad:
                invalid += 1
            if ok:
                successful += 1
                for path in hits:
                    if path not in seen_matched:
                        seen_matched.add(path)
                        matched.append(path)
            else:
                failed += 1
            continue
        if is_read_command(command) and not command_failed(item):
            for path in extract_read_paths(command):
                if path not in seen_inspected:
                    seen_inspected.add(path)
                    inspected.append(path)

    for path in _read_paths_from_logs(logs):
        if path not in seen_inspected:
            seen_inspected.add(path)
            inspected.append(path)

    has_tools = tool_activity is not None
    return InvestigationSummary(
        files_inspected=len(inspected) if has_tools or inspected else None,
        inspected_paths=inspected,
        search_matched_files=len(matched) if has_tools or matched else None,
        search_matched_paths=matched,
        tool_calls=tool_calls if has_tools else None,
        search_commands=commands,
        successful_searches=successful,
        failed_searches=failed,
        invalid_windows_globs=invalid,
    )


def is_search_command(command: str) -> bool:
    return bool(_SEARCH_RE.search(str(command or "")))


def is_read_command(command: str) -> bool:
    text = str(command or "")
    if is_search_command(text):
        return False
    return bool(_READ_RE.search(text))


def command_failed(item: dict[str, Any]) -> bool:
    status = str(item.get("status") or "").lower()
    if status in {"failed", "error", "errored"}:
        return True
    detail = str(item.get("detail") or item.get("output") or "")
    if _FAILED_RE.search(detail):
        return True
    if has_invalid_windows_search_glob(str(item.get("name") or item.get("command") or "")) and (
        "error" in detail.lower() or "failed" in status or not detail.strip()
    ):
        # Invalid glob with empty/error output is not a successful search.
        if "error" in detail.lower() or status in {"failed", "error"}:
            return True
    return False


def has_invalid_windows_search_glob(command: str) -> bool:
    """Detect PowerShell-unsafe globs such as `tests *.py` or `lookup/test_*`."""
    cmd = str(command or "").strip()
    if not cmd:
        return False
    stripped = _GLOB_FLAG_RE.sub(" ", cmd)
    if re.search(r"(?:^|\s)(?:tests|lookup|src|pkg)\s+\*\.\w+", stripped, re.I):
        return True
    if re.search(r"(?:^|\s)\*\.\w+", stripped):
        return True
    if re.search(r"(?:^|\s)[\w./\\-]+\*(?:\s|$)", stripped):
        return True
    return bool(_INVALID_GLOB_RE.search(stripped))


def extract_paths(blob: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    text = str(blob or "").replace("\\", "/")
    for match in list(_PATH_RE.finditer(text)) + list(_SIMPLE_FILE_RE.finditer(text)):
        raw = match.group(0) if match.lastindex is None or match.lastindex == 0 else match.group(match.lastindex)
        path = _normalize_path(raw)
        if not path or path in seen:
            continue
        seen.add(path)
        out.append(path)
    return out


def extract_read_paths(command: str) -> list[str]:
    """Paths from a file-read command. Never use search stdout here."""
    text = str(command or "")
    out: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        path = _normalize_path(raw)
        if not path or path in seen:
            return
        seen.add(path)
        out.append(path)

    for path in extract_paths(text):
        add(path)
    for match in _PATH_FLAG_RE.finditer(text):
        raw = re.sub(r"^(?:-(?:Path|LiteralPath)|--path)\s+", "", match.group(0), flags=re.I)
        add(raw)
    tail_match = _READ_TAIL_RE.search(text)
    if tail_match:
        tail = tail_match.group(1) or ""
        for token in re.findall(r"""'[^']*'|"[^"]*"|\S+""", tail):
            stripped = token.strip()
            if not stripped or stripped.startswith("-") or stripped.startswith("("):
                continue
            add(stripped)
    if not out:
        for match in _BARE_FILE_RE.finditer(text.replace("\\", "/")):
            add(match.group(1))
    return out


def _read_paths_from_logs(logs: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for line in str(logs or "").splitlines():
        if not re.search(r"^\[(?:tool|codex_investigation)\]", line, re.I) and not is_read_command(line):
            continue
        if is_search_command(line):
            continue
        if not is_read_command(line) and "Reading repository file" not in line:
            # [tool] Get-Content path (completed)
            if not re.search(r"\[tool\].*(?:get-content|gc|cat|sed|type)\b", line, re.I):
                continue
        for path in extract_read_paths(line):
            if path not in seen:
                seen.add(path)
                out.append(path)
    return out


def _normalize_path(raw: str) -> str:
    path = str(raw or "").replace("\\", "/").strip().strip("\"'`")
    path = path.strip("()[]{},")
    while path.startswith("./"):
        path = path[2:]
    path = path.lstrip("/")
    if not path or len(path) > 220:
        return ""
    lower = path.lower()
    if any(part in lower for part in _SKIP_PATH_PARTS):
        return ""
    if "://" in path:
        return ""
    name = path.rsplit("/", 1)[-1]
    if "." not in name or name in {"completed", "failed", "error"}:
        return ""
    return path
