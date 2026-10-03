"""Readers for the record files ``simulate`` and ``diff`` replay."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .errors import DataError


def read_records(path: str | Path) -> Iterator[dict[str, Any]]:
    """Stream records from a ``.csv``, ``.jsonl`` / ``.ndjson`` or ``.json`` (array) file.

    Records are yielded raw; the policy schema coerces CSV text to typed values.

    Raises:
        DataError: if the file is missing, has an unsupported extension, or is malformed.
    """
    p = Path(path)
    suffix = p.suffix.lower()
    readers = {".csv": _csv, ".jsonl": _jsonl, ".ndjson": _jsonl, ".json": _json}
    if suffix not in readers:
        raise DataError(
            f"unsupported data file type {suffix or '(none)'!r}; use .csv, .jsonl or .json"
        )
    if not p.is_file():
        raise DataError(f"data file not found: {p}")
    yield from readers[suffix](p)


def _csv(path: Path) -> Iterator[dict[str, Any]]:
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise DataError(f"{path}: CSV file is empty")
            for row in reader:
                if not any((v or "").strip() for v in row.values() if isinstance(v, str)):
                    continue  # blank line
                # Short rows leave None; extra cells land under a None key. Keep named columns only.
                yield {k: v for k, v in row.items() if k is not None}
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise DataError(f"{path}: cannot read CSV: {exc}") from exc


def _jsonl(path: Path) -> Iterator[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise DataError(f"{path}:{number}: invalid JSON: {exc.msg}") from exc
                if not isinstance(record, dict):
                    raise DataError(f"{path}:{number}: each line must be a JSON object")
                yield record
    except (OSError, UnicodeDecodeError) as exc:
        raise DataError(f"{path}: cannot read file: {exc}") from exc


def _json(path: Path) -> Iterator[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise DataError(f"{path}: cannot read file: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise DataError(f"{path}: invalid JSON: {exc.msg} (line {exc.lineno})") from exc
    if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
        raise DataError(f"{path}: expected a JSON array of objects")
    yield from data
