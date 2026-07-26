"""Read-only hub simulator CLI (demo). Reads packed prompt from a file."""

from __future__ import annotations

import argparse
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hub.agent_center.simulator")
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--mode", default="ask")
    parser.add_argument("--model", default="simulator")
    parser.add_argument("--cwd", default="")
    args = parser.parse_args(argv)

    path = Path(args.prompt_file)
    text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    user = _extract_user_prompt(text)
    roots = [line.strip() for line in text.splitlines() if line.strip().startswith("- ") and ": " in line]

    print("Hub Simulator · demo (read-only)")
    print(f"mode={args.mode} model={args.model}")
    if args.cwd:
        print(f"cwd={args.cwd}")
    print(f"packed_prompt_chars={len(text)}")
    print(f"user_prompt={user!r}"[:500])
    if roots:
        print("repository_roots:")
        for line in roots[:8]:
            print(f"  {line}")
    print()
    print("Answer:")
    print(
        "Yes — Agent Center is working. Context was packed, the run started, "
        "and this local simulator produced the answer without calling an external agent CLI."
    )
    print(
        "Install `claude`, Cursor `agent`/`cursor-agent`, or `codex` on PATH to use real agents. "
        "The IDE `cursor` binary is not an agent CLI."
    )
    return 0


def _extract_user_prompt(packed: str) -> str:
    marker = "# User prompt\n"
    if marker in packed:
        return packed.split(marker, 1)[1].strip()
    return packed.strip()[:200]


if __name__ == "__main__":
    raise SystemExit(main())
