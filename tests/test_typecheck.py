from __future__ import annotations

import pytest

from rulelens.errors import ExpressionError
from rulelens.parser import parse
from rulelens.typecheck import typecheck

TYPES = {"income": "number", "name": "string", "ok": "bool", "opened": "date", "closed": "date"}


def check(source: str) -> str:
    return typecheck(parse(source), source, TYPES.get, TYPES)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("income > 5", "bool"),
        ("income * 2 + 1", "number"),
        ("name == 'x' and ok", "bool"),
        ("name in ['a', 'b']", "bool"),
        ("income is null", "bool"),
        ("income > 5 or name is not null", "bool"),
        ("days_between(opened, closed) > 30", "bool"),
        ("opened < closed", "bool"),
        ("opened < date('2025-01-01')", "bool"),
        ("coalesce(income, 0)", "number"),
        ("max(income, 3, 4)", "number"),
        ("between(income, 1, 2)", "bool"),
        ("round(income)", "number"),
        ("round(income, 2)", "number"),
        ("lower(name)", "string"),
    ],
)
def test_valid_expressions(source: str, expected: str) -> None:
    assert check(source) == expected


@pytest.mark.parametrize(
    ("source", "fragment"),
    [
        ("incom > 5", "did you mean 'income'"),
        ("unknown > 5", "unknown field 'unknown'"),
        ("income < 'high'", "cannot compare number < string"),
        ("name + 1", "needs numbers"),
        ("income and ok", "needs boolean operands"),
        ("not income", "'not' needs a boolean"),
        ("-name", "unary '-' needs a number"),
        ("ok > ok", "cannot order booleans"),
        ("income == null", "is null"),
        ("income in 5", "must be a list"),
        ("income in ['a']", "cannot test number against a list of string"),
        ("income in [1, 'a']", "share one type"),
        ("income in [null]", "plain values"),
        ("[1] == [1]", "lists can only be used with 'in'"),
        ("lenn(name)", "did you mean 'len'"),
        ("len(income)", "argument 1 must be string"),
        ("len(name, name)", "takes 1 argument"),
        ("round()", "takes 1 to 2"),
        ("between(income, 'a', 2)", "must all have the same type"),
        ("between(opened, closed, income)", "must all have the same type"),
        ("between(ok, ok, ok)", "argument 1 must be"),
        ("coalesce(income, name)", "share one type"),
        ("min(1)", "takes 2 argument"),
    ],
)
def test_type_errors_are_precise(source: str, fragment: str) -> None:
    with pytest.raises(ExpressionError) as info:
        check(source)
    assert fragment in str(info.value)


def test_error_span_points_at_offending_operand() -> None:
    with pytest.raises(ExpressionError) as info:
        check("income > 5 and name + 1 > 2")
    err = info.value
    assert err.source[err.start : err.end] == "name"
