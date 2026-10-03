"""Field schema: declared types and coercion of raw input values.

Input arrives from CSV (everything is text), JSON (typed) or Python dicts.
``coerce`` turns it into the typed values rules operate on. A value that cannot
be interpreted becomes *unknown* (``None``) together with a problem message, so
one bad cell degrades a decision gracefully instead of crashing a batch run.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Final

from .nodes import Value

DECLARED_TYPES: Final = ("int", "number", "string", "bool", "date")

_TRUE: Final = frozenset({"true", "yes", "y", "1", "t"})
_FALSE: Final = frozenset({"false", "no", "n", "0", "f"})
_MISSING: Final = object()


def static_type(declared: str) -> str:
    """Map a declared schema type to the type-checker's vocabulary."""
    return "number" if declared == "int" else declared


def flatten_schema(raw: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Flatten nested TOML tables (``applicant.income = "number"``) into dotted names."""
    flat: dict[str, Any] = {}
    for key, value in raw.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(flatten_schema(value, f"{name}."))
        else:
            flat[name] = value
    return flat


def lookup(record: Any, name: str) -> Any:
    """Find ``name`` in a record, as a flat key first and then as a nested path."""
    try:
        direct = record.get(name, _MISSING)
    except AttributeError:
        return None
    if direct is not _MISSING:
        return direct
    if "." not in name:
        return None
    node: Any = record
    for part in name.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def coerce(value: Any, declared: str) -> tuple[Value, str | None]:
    """Convert ``value`` to ``declared``; return ``(value, problem)``.

    ``problem`` is ``None`` on success. On failure the value is ``None`` (unknown).
    """
    if value is None:
        return None, None
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return None, None
    else:
        text = ""

    if declared == "string":
        if isinstance(value, str):
            return value, None
        if isinstance(value, bool | int | float):
            return str(value), None
        return None, f"cannot interpret {type(value).__name__} as string"

    if declared in ("int", "number"):
        number = _to_number(value, text)
        if number is None:
            return None, f"cannot interpret {value!r} as {declared}"
        if declared == "int" and isinstance(number, float):
            if not number.is_integer():
                return None, f"cannot interpret {value!r} as int"
            number = int(number)
        return number, None

    if declared == "bool":
        if isinstance(value, bool):
            return value, None
        key = text.lower() if text else str(value).lower()
        if key in _TRUE:
            return True, None
        if key in _FALSE:
            return False, None
        return None, f"cannot interpret {value!r} as bool"

    if declared == "date":
        if isinstance(value, datetime):
            return value.date(), None
        if isinstance(value, date):
            return value, None
        if text:
            try:
                return date.fromisoformat(text[:10]), None
            except ValueError:
                pass
        return None, f"cannot interpret {value!r} as an ISO date (YYYY-MM-DD)"

    raise ValueError(f"unknown declared type {declared!r}")  # pragma: no cover


def _to_number(value: Any, text: str) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if text:
        try:
            return int(text)
        except ValueError:
            pass
        try:
            parsed = float(text)
        except ValueError:
            return None
        return parsed if math.isfinite(parsed) else None
    return None
