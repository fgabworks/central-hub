"""Sample capability script for Central Hub command jobs (demo only)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    job_id = os.environ.get("CENTRAL_HUB_JOB_ID", "unknown")
    dry_run = os.environ.get("CENTRAL_HUB_DRY_RUN", "1") == "1"
    result_dir = Path(os.environ.get("CENTRAL_HUB_RESULT_DIR") or ".")
    input_dir = Path(os.environ.get("CENTRAL_HUB_INPUT_DIR") or ".")
    result_dir.mkdir(parents=True, exist_ok=True)

    inputs = sorted(p.name for p in input_dir.iterdir() if p.is_file()) if input_dir.is_dir() else []
    payload = {
        "job_id": job_id,
        "dry_run": dry_run,
        "mode": "dry-run" if dry_run else "apply",
        "inputs": inputs,
        "message": "Echo capability completed without side effects outside result dir.",
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    out = result_dir / ("echo_dry_run.json" if dry_run else "echo_apply.json")
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"ok job={job_id} dry_run={dry_run} wrote={out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
