from __future__ import annotations

import math

import pytest

from rulelens import Policy
from rulelens.analysis import (
    MAX_TERMS,
    Cat,
    Interval,
    Numeric,
    abstract,
    implies,
    is_unsatisfiable,
    lint,
)
from rulelens.parser import parse

TYPES = {"x": "number", "y": "number", "s": "string", "ok": "bool", "d": "date"}
INF = math.inf


def model(source: str):  # type: ignore[no-untyped-def]
    result = abstract(parse(source), TYPES)
    assert result is not None
    return result


def iv(lo: float, lo_closed: bool, hi: float, hi_closed: bool) -> Interval:
    return Interval(lo, lo_closed, hi, hi_closed)


# -- interval sets ------------------------------------------------------------------------------


def test_touching_intervals_merge_only_when_the_join_point_is_included() -> None:
    assert Numeric.of([iv(1, True, 2, True), iv(2, False, 3, True)]).intervals == (
        iv(1, True, 3, True),
    )
    assert len(Numeric.of([iv(1, True, 2, False), iv(2, False, 3, True)]).intervals) == 2
    assert Numeric.of([iv(1, True, 2, False), iv(2, True, 3, True)]).intervals == (
        iv(1, True, 3, True),
    )


def test_complement_of_a_point_is_two_open_rays() -> None:
    point = Numeric.of([iv(5, True, 5, True)])
    assert point.complement().intervals == (iv(-INF, False, 5, False), iv(5, False, INF, False))
    assert point.complement().complement() == point


def test_intersect_picks_the_tighter_open_or_closed_bound() -> None:
    a = Numeric.of([iv(0, True, 10, True)])
    b = Numeric.of([iv(0, False, 10, False)])
    assert a.intersect(b).intervals == (iv(0, False, 10, False),)
    assert a.intersect(Numeric.of([iv(10, True, 20, True)])).intervals == (iv(10, True, 10, True),)
    assert a.intersect(Numeric.of([iv(10, False, 20, True)])).is_empty()


def test_subset() -> None:
    assert Numeric.of([iv(1, True, 2, True)]).subset_of(Numeric.of([iv(0, False, 3, False)]))
    assert not Numeric.of([iv(0, True, 2, True)]).subset_of(Numeric.of([iv(0, False, 3, False)]))
    assert Numeric.of([]).subset_of(Numeric.of([]))


def test_cat_operations() -> None:
    a, b = Cat.of({"x", "y"}, False, None), Cat.of({"y", "z"}, False, None)
    assert a.intersect(b).values == {"y"}
    assert a.intersect(b.complement()).values == {"x"}
    assert a.complement().intersect(b.complement()) == Cat.of({"x", "y", "z"}, True, None)
    assert Cat.of({"x"}, False, None).subset_of(a)
    assert Cat.of({"q"}, False, None).subset_of(a.complement())
    assert not Cat.of({"x"}, False, None).subset_of(a.complement())
    assert a.complement().subset_of(Cat.of({"x"}, True, None))
    assert not a.complement().subset_of(a)  # cofinite never fits in finite
    assert Cat.of(set(), False, None).is_empty()


def test_boolean_domain_has_a_finite_universe() -> None:
    both = frozenset({True, False})
    only_true = Cat.of({True}, False, both)
    assert only_true.complement().values == {False}
    assert only_true.intersect(only_true.complement()).is_empty()
    assert Cat.of({True, False}, True, both).is_empty()


# -- abstraction and implication ---------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "x > 5 and x < 3",
        "x == 1 and x == 2",
        "s == 'a' and s == 'b'",
        "s in ['a', 'b'] and s not in ['a', 'b']",
        "ok and not ok",
        "x > 5 and not (x > 2)",
        "false",
        "not true",
        "x < 1 and (x > 4 or x > 6)",
        "x >= 5 and x < 5",
        "5 < x and x <= 5",  # literal on the left is flipped
    ],
)
def test_unsatisfiable(source: str) -> None:
    assert is_unsatisfiable(model(source))


@pytest.mark.parametrize(
    "source",
    [
        "x > 5",
        "x > 5 or x < 3",
        "x >= 5 and x <= 5",
        "s != 'a'",
        "ok",
        "x + y > 3",
        "x is null",
        "x > 5 and x + y > 3",
        "true",
        "x > 5 and y < 1",
    ],
)
def test_satisfiable(source: str) -> None:
    assert not is_unsatisfiable(model(source))


def test_contradiction_names_the_field() -> None:
    assert model("x > 5 and x < 3 and s == 'a'").contradictions == ("x",)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("x > 10", "x > 5"),
        ("x >= 6 and y < 3", "x > 5"),
        ("x == 7", "x in [7, 8]"),
        ("s == 'a'", "s in ['a', 'b']"),
        ("s == 'a'", "s != 'b'"),
        ("ok", "not (not ok)"),
        ("x > 10 or x < -10", "x > 5 or x < -5"),
        ("x > 5 and x + y > 3", "x > 3"),  # opaque part only narrows a
        ("not (x <= 5)", "x > 5"),
        ("x > 1", "x > 0 or y > 0"),
        ("x > 5 and y > 5", "not (x <= 5 or y <= 5)"),
    ],
)
def test_implies(a: str, b: str) -> None:
    assert implies(model(a), model(b))


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("x > 5", "x > 10"),
        ("x > 5", "x > 5 and y > 0"),  # b needs y to exist
        ("x > 5", "x > 3 and x + y > 3"),  # b has an opaque requirement
        ("x > 5 or y > 5", "x > 5"),
        ("s != 'a'", "s == 'b'"),
        ("x > 5", "x < 3 or x > 4 and x < 5"),  # covered only by a union: incomplete but sound
        ("x is null", "x < 3"),
        ("x < 3 or x >= 3", "x > 5"),
    ],
)
def test_does_not_imply(a: str, b: str) -> None:
    assert not implies(model(a), model(b))


def test_requires_presence_even_when_the_domain_is_everything() -> None:
    # `x < 5 or x >= 5` is true for every *present* x but unknown when x is missing, so a rule
    # that never mentions x is not covered by it.
    assert not implies(model("y > 1"), model("x < 5 or x >= 5"))
    assert implies(model("x == 1"), model("x < 5 or x >= 5"))


def test_huge_disjunctions_are_skipped_rather_than_exploding() -> None:
    # Independent fields, so no term dies early: 2**7 = 128 boxes exceeds MAX_TERMS (64).
    fields = {f"f{i}": "number" for i in range(7)}
    expr = " and ".join(f"({name} == 1 or {name} == 2)" for name in fields)
    assert MAX_TERMS < 2**7
    assert abstract(parse(expr), fields) is None
    assert abstract(parse(expr.rsplit(" and ", 1)[0]), fields) is not None  # 2**6 = 64 still fits


# -- lint ------------------------------------------------------------------------------------


def policy(strategy: str, rules: str, extra: str = "") -> Policy:
    return Policy.from_toml(
        f"""
[policy]
name = "t"
strategy = "{strategy}"
outcomes = ["low", "mid", "high"]
default = "low"
{extra}
[schema]
x = "number"
y = "number"
s = "string"
unused = "bool"
[derived]
twice = "x * 2"
{rules}
"""
    )


def rule(rid: str, when: str, outcome: str) -> str:
    return f'[[rules]]\nid = "{rid}"\nwhen = "{when}"\noutcome = "{outcome}"\nreason = "r"\n'


def found(p: Policy) -> dict[str, str]:
    return {d.rule_id or d.location or "": d.code for d in lint(p)}


def test_lint_unsatisfiable_rule() -> None:
    p = policy("first_match", rule("DEAD", "x > 5 and x < 3", "high"))
    diag = next(d for d in lint(p) if d.code == "W101")
    assert diag.rule_id == "DEAD" and "'x'" in diag.message


def test_lint_shadowed_rule_in_first_match() -> None:
    p = policy(
        "first_match",
        rule("BROAD", "x > 5", "high")
        + rule("NARROW", "x > 10 and y < 3", "mid")
        + rule("OK", "y < 0", "low"),
    )
    codes = found(p)
    assert codes["NARROW"] == "W102"
    assert "OK" not in codes
    assert "BROAD" in next(d for d in lint(p) if d.code == "W102").message


def test_first_match_order_matters_for_shadowing() -> None:
    p = policy("first_match", rule("NARROW", "x > 10", "mid") + rule("BROAD", "x > 5", "high"))
    assert "W102" not in {d.code for d in lint(p)}


def test_lint_redundant_rule_in_most_severe() -> None:
    p = policy("most_severe", rule("MID", "x > 10", "mid") + rule("HIGH", "x > 5", "high"))
    diag = next(d for d in lint(p) if d.code == "W103")
    assert diag.rule_id == "MID" and "HIGH" in diag.message


def test_most_severe_is_order_independent_and_ignores_equal_or_lower_severity() -> None:
    p = policy("most_severe", rule("HIGH", "x > 10", "high") + rule("MID", "x > 5", "mid"))
    assert "W103" not in {d.code for d in lint(p)}
    p = policy("most_severe", rule("A", "x > 10", "mid") + rule("B", "x > 5", "mid"))
    assert "W103" not in {d.code for d in lint(p)}


def test_lint_reports_unused_fields_and_missing_tests() -> None:
    p = policy("first_match", rule("R", "x > 5", "high"))
    by_code = {d.code: d for d in lint(p)}
    assert by_code["I201"].location == "schema.unused" or any(
        d.location == "schema.unused" for d in lint(p)
    )
    assert any(d.code == "I202" and "twice" in d.message for d in lint(p))
    assert "I203" in by_code


def test_fields_used_only_in_reason_templates_or_derived_count_as_used() -> None:
    text = rule("R", "twice > 5", "high").replace('reason = "r"', 'reason = "{y} {s}"')
    p = policy("first_match", text)
    unused = {d.location for d in lint(p) if d.code in ("I201", "I202")}
    assert unused == {"schema.unused"}


def test_lint_is_silent_about_unanalysable_rules() -> None:
    p = policy("first_match", rule("A", "x + y > 5", "high") + rule("B", "x + y > 9", "mid"))
    assert not {d.code for d in lint(p)} & {"W101", "W102", "W103"}


def test_derived_fields_are_analysed_like_fields() -> None:
    p = policy("first_match", rule("A", "twice > 5", "high") + rule("B", "twice > 9", "mid"))
    assert found(p)["B"] == "W102"
