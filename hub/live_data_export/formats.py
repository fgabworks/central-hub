"""Write export files (CSV / XLSX / gzip CSV). Never log row contents."""

from __future__ import annotations

import csv
import gzip
import io
from pathlib import Path
from typing import Any, Iterable, Sequence


def write_csv(path: Path, columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(list(columns))
        count = 0
        for row in rows:
            writer.writerow([_cell(v) for v in row])
            count += 1
    return path.stat().st_size


def write_csv_gz(path: Path, columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(list(columns))
        for row in rows:
            writer.writerow([_cell(v) for v in row])
    return path.stat().st_size


def write_xlsx(path: Path, columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> int:
    try:
        from openpyxl import Workbook
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("openpyxl is required for XLSX exports") from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("export")
    ws.append(list(columns))
    for row in rows:
        ws.append([_cell(v) for v in row])
    wb.save(path)
    return path.stat().st_size


def rows_to_csv_bytes(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(list(columns))
    for row in rows:
        writer.writerow([_cell(v) for v in row])
    return buf.getvalue().encode("utf-8")


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return value
