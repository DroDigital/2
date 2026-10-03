from __future__ import annotations

import itertools
from datetime import date
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rulelens.evaluator import evaluate, kind_of
from rulelens.nodes import Value
from rulelens.parser import parse


def run(source: str, **values: Value) -> tuple[Value, list[str]]:
    warnings: list[str] = []
    result = evaluate(parse(source), lambda path: values.get(".".join(path)), warnings)
    return result, warnings


def value(source: str, **values: Value) -> Value:
    return run(source, **values)[0]


def test_arithmetic_and_comparison() -> None:
    assert value("1 + 2 * 3 == 7") is True
    assert value("10 % 4") == 2
    assert value("7 / 2") == 3.5
    assert value("income * 0.1 > 100", income=2000) is True


def test_string_and_membership() -> None:
    assert value("name == 'ann'", name="ann") is True
    assert value("name in ['ann', 'bob']", name="bob") is True
    assert value("name not in ['ann', 'bob']", name="cy") is True
    assert value("'a' < 'b'") is True


def test_values_of_different_kinds_are_never_equal() -> None:
    assert value("1 == true") is False
    assert value("x == '1'", x=1) is False
    assert value("x != '1'", x=1) is True


def test_field_lookup_uses_dotted_path() -> None:
    assert value("a.b > 1", **{"a.b": 5}) is True


# -- Kleene three-valued logic ------------------------------------------------------------------

T, F, U = True, False, None
AND = {
    (T, T): T,
    (T, F): F,
    (T, U): U,
    (F, T): F,
    (F, F): F,
    (F, U): F,
    (U, T): U,
    (U, F): F,
    (U, U): U,
}
OR = {
    (T, T): T,
    (T, F): T,
    (T, U): T,
    (F, T): T,
    (F, F): F,
    (F, U): U,
    (U, T): T,
    (U, F): U,
    (U, U): U,
}


@pytest.mark.parametrize(("a", "b"), list(itertools.product([T, F, U], repeat=2)))
def test_kleene_truth_tables(a: bool | None, b: bool | None) -> None:
    assert value("a and b", a=a, b=b) is AND[(a, b)]
    assert value("a or b", a=a, b=b) is OR[(a, b)]


@pytest.mark.parametrize(("a", "expected"), [(T, F), (F, T), (U, U)])
def test_kleene_not(a: bool | None, expected: bool | None) -> None:
    assert value("not a", a=a) is expected


def test_unknown_propagates_through_comparisons_and_arithmetic() -> None:
    assert value("x > 1") is None
    assert value("x + 1 > 1") is None
    assert value("x in [1, 2]") is None
    assert value("x == x") is None


def test_is_null_is_always_definite() -> None:
    assert value("x is null") is True
    assert value("x is not null") is False
    assert value("x is null", x=0) is False


# -- graceful degradation -----------------------------------------------------------------------


def test_division_by_zero_is_unknown_with_warning() -> None:
    result, warnings = run("1 / x > 0", x=0)
    assert result is None
    assert "division by zero" in warnings[0]
    assert run("5 % 0")[0] is None


def test_type_mismatch_is_unknown_with_warning() -> None:
    result, warnings = run("x < 3", x="abc")
    assert result is None
    assert "cannot order string < number" in warnings[0]
    assert run("x + 1", x="abc")[0] is None
    assert run("-x", x="abc")[0] is None
    assert run("true < false")[0] is None


def test_non_boolean_in_logic_is_unknown() -> None:
    result, warnings = run("x and true", x=5)
    assert result is None
    assert "expected a boolean" in warnings[0]
    assert run("not x", x=5)[0] is None


def test_in_requires_a_list_at_runtime() -> None:
    result, warnings = run("1 in x", x=5)
    assert result is None
    assert "must be a list" in warnings[0]


# -- functions ----------------------------------------------------------------------------------


def test_functions() -> None:
    assert value("abs(-3)") == 3
    assert value("min(3, 1, 2)") == 1
    assert value("max(3, 1, 2)") == 3
    assert value("round(2.675, 1)") == 2.7
    assert value("round(2.5)") == 2  # half-to-even, documented
    assert value("len('abc')") == 3
    assert value("lower('AbC')") == "abc"
    assert value("upper('AbC')") == "ABC"
    assert value("contains('hello', 'ell')") is True
    assert value("startswith('hello', 'he')") is True
    assert value("endswith('hello', 'lo')") is True
    assert value("between(5, 1, 10)") is True
    assert value("between(11, 1, 10)") is False
    assert value("coalesce(x, y, 3)", y=None) == 3
    assert value("coalesce(x, y)", y=None) is None


def test_null_arguments_propagate() -> None:
    assert value("abs(x)") is None
    assert value("lower(x)") is None


def test_dates() -> None:
    assert value("days_between(a, b)", a=date(2025, 1, 1), b=date(2025, 1, 31)) == 30
    assert value("a < b", a=date(2025, 1, 1), b=date(2025, 1, 2)) is True
    assert value("a == date('2025-01-01')", a=date(2025, 1, 1)) is True
    result, warnings = run("date('not-a-date')")
    assert result is None
    assert "not an ISO date" in warnings[0]


def test_function_runtime_failure_is_unknown() -> None:
    result, warnings = run("abs(x)", x="oops")
    assert result is None
    assert "abs() failed" in warnings[0]


@pytest.mark.parametrize(
    ("v", "kind"),
    [
        (None, "null"),
        (True, "bool"),
        (1, "number"),
        (1.5, "number"),
        ("s", "string"),
        (date(2025, 1, 1), "date"),
        ((1, 2), "list"),
    ],
)
def test_kind_of(v: Value, kind: str) -> None:
    assert kind_of(v) == kind


# -- differential test against Python's own semantics -------------------------------------------

_vars = {"x": 7, "y": -3, "z": 12}


def _int_expr() -> st.SearchStrategy[str]:
    leaf = st.one_of(st.integers(0, 50).map(str), st.sampled_from(sorted(_vars)))
    return st.recursive(
        leaf,
        lambda c: st.one_of(
            st.tuples(c, st.sampled_from(["+", "-", "*"]), c).map(
                lambda t: f"({t[0]} {t[1]} {t[2]})"
            ),
            st.tuples(c, st.integers(1, 9)).map(lambda t: f"({t[0]} % {t[1]})"),
        ),
        max_leaves=6,
    )


def _bool_expr() -> st.SearchStrategy[str]:
    cmp_ = st.tuples(
        _int_expr(), st.sampled_from(["==", "!=", "<", "<=", ">", ">="]), _int_expr()
    ).map(lambda t: f"({t[0]} {t[1]} {t[2]})")
    membership = st.tuples(_int_expr(), st.lists(st.integers(-5, 20), min_size=1, max_size=4)).map(
        lambda t: f"({t[0]} in {t[1]})"
    )
    return st.recursive(
        st.one_of(cmp_, membership, st.sampled_from(["true", "false"])),
        lambda c: st.one_of(
            st.tuples(c, st.sampled_from(["and", "or"]), c).map(
                lambda t: f"({t[0]} {t[1]} {t[2]})"
            ),
            c.map(lambda e: f"(not {e})"),
        ),
        max_leaves=6,
    )


@given(_bool_expr())
def test_agrees_with_python_when_no_data_is_missing(source: str) -> None:
    namespace: dict[str, Any] = {**_vars, "true": True, "false": False}
    expected = eval(source, {"__builtins__": {}}, namespace)
    assert value(source, **_vars) is expected
