"""Whitelisted, pure functions available inside rule expressions.

Each function declares a signature so the type checker can validate calls
before any data is seen, and so the evaluator can propagate ``null`` uniformly.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, Final

# Static types understood by the checker. "any" accepts every type.
ANY: Final = "any"


@dataclass(frozen=True, slots=True)
class FunctionSpec:
    name: str
    params: tuple[frozenset[str], ...]  # accepted static types per positional parameter
    returns: str
    impl: Callable[..., Any]
    variadic: bool = False  # the last parameter spec repeats (at least once)
    optional: int = 0  # number of trailing parameters that may be omitted
    null_propagating: bool = True  # any null argument yields null


def _t(*names: str) -> frozenset[str]:
    return frozenset(names)


_NUM = _t("number")
_STR = _t("string")
_DATE = _t("date")
_ORDERED = _t("number", "string", "date")
_EVERYTHING = _t(ANY)


def _round(x: float, digits: int = 0) -> float:
    # Python rounds half to even ("banker's rounding"); documented in docs/language.md.
    return round(x, int(digits)) if digits else float(round(x))


def _parse_date(text: str) -> date | None:
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _coalesce(*values: Any) -> Any:
    for v in values:
        if v is not None:
            return v
    return None


FUNCTIONS: Final[dict[str, FunctionSpec]] = {
    spec.name: spec
    for spec in (
        FunctionSpec("abs", (_NUM,), "number", abs),
        FunctionSpec("round", (_NUM, _NUM), "number", _round, optional=1),
        FunctionSpec("min", (_NUM, _NUM), "number", lambda *a: min(a), variadic=True),
        FunctionSpec("max", (_NUM, _NUM), "number", lambda *a: max(a), variadic=True),
        FunctionSpec("len", (_STR,), "number", len),
        FunctionSpec("lower", (_STR,), "string", str.lower),
        FunctionSpec("upper", (_STR,), "string", str.upper),
        FunctionSpec("contains", (_STR, _STR), "bool", lambda s, sub: sub in s),
        FunctionSpec("startswith", (_STR, _STR), "bool", str.startswith),
        FunctionSpec("endswith", (_STR, _STR), "bool", str.endswith),
        FunctionSpec(
            "between", (_ORDERED, _ORDERED, _ORDERED), "bool", lambda x, lo, hi: lo <= x <= hi
        ),
        FunctionSpec("days_between", (_DATE, _DATE), "number", lambda a, b: (b - a).days),
        FunctionSpec("date", (_STR,), "date", _parse_date),
        FunctionSpec(
            "coalesce",
            (_EVERYTHING, _EVERYTHING),
            ANY,
            _coalesce,
            variadic=True,
            null_propagating=False,
        ),
    )
}
