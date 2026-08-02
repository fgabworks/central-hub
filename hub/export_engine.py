"""Shared file writer for all Data Explorer exports."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hub.live_data_export.formats import write_csv, write_csv_gz, write_xlsx


def write_export(
    path: Path,
    columns: list[str],
    rows: list[list[Any]],
    *,
    format: str,
) -> int:
    fmt = str(format or "csv").lower()
    if fmt == "xlsx":
        return write_xlsx(path, columns, rows)
    if fmt == "csv_gz":
        return write_csv_gz(path, columns, rows)
    if fmt == "csv":
        return write_csv(path, columns, rows)
    raise ValueError(f"Unsupported export format: {fmt}")
