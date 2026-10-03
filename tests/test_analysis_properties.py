"""The analyzer's claims, checked against the evaluator.

Every finding must be *true*. We generate small random policies over a handful
of fields, then enumerate a grid of records (including missing values) that
covers every region the literals can distinguish, and check:

* a rule reported unsatisfiable never fires on any record;
* if ``implies(A, B)`` then every record firing A also fires B.
"""

from __future__ import annotations

import itertools
import random
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from rulelens.analysis import abstract, implies, is_unsatisfiable
from rulelens.evaluator import evaluate
from rulelens.nodes import Node
from rulelens.parser import parse

TYPES = {"x": "number", "y": "number", "s": "string", "ok": "bool"}

# Literals stay within [-3, 3], so integers in [-4, 4] hit every distinguishable region.
GRID: list[dict[str, Any]] = [
    {"x": x, "y": y, "s": s, "ok": ok}
    for x, y, s, ok in itertools.product(
        [None, *range(-4, 5)],
        [None, *range(-4, 5)],
        [None, "a", "b", "c", "d"],
        [None, True, False],
    )
]


def fires(node: Node, record: dict[str, Any]) -> bool:
    return evaluate(node, lambda path: record.get(path[0])) is True


def _atoms() -> st.SearchStrategy[str]:
    num = st.integers(-3, 3)
    field = st.sampled_from(["x", "y"])
    cmp_ = st.tuples(field, st.sampled_from(["<", "<=", ">", ">=", "==", "!="]), num).map(
        lambda t: f"{t[0]} {t[1]} {t[2]}"
    )
    flipped = st.tuples(num, st.sampled_from(["<", "<=", ">", ">="]), field).map(
        lambda t: f"{t[0]} {t[1]} {t[2]}"
    )
    members = st.tuples(field, st.lists(num, min_size=1, max_size=3), st.booleans()).map(
        lambda t: f"{t[0]} {'not in' if t[2] else 'in'} {t[1]}"
    )
    strings = st.tuples(st.sampled_from(["==", "!="]), st.sampled_from(["a", "b", "c"])).map(
        lambda t: f"s {t[0]} '{t[1]}'"
    )
    smembers = st.tuples(
        st.lists(st.sampled_from(["a", "b", "c"]), min_size=1, max_size=2), st.booleans()
    ).map(lambda t: f"s {'not in' if t[1] else 'in'} {t[0]}")
    bools = st.sampled_from(["ok", "not ok", "ok == true", "ok != true"])
    opaque = st.sampled_from(["x + y > 0", "x is null", "x < y", "abs(x) > 1", "s is not null"])
    return st.one_of(cmp_, cmp_, flipped, members, strings, smembers, bools, opaque)


def _expressions() -> st.SearchStrategy[str]:
    return st.recursive(
        _atoms(),
        lambda c: st.one_of(
            st.tuples(c, st.sampled_from(["and", "or"]), c).map(
                lambda t: f"({t[0]} {t[1]} {t[2]})"
            ),
            c.map(lambda e: f"not ({e})"),
        ),
        max_leaves=5,
    )


@settings(max_examples=250, deadline=None)
@given(_expressions())
def test_unsatisfiable_claims_are_true(source: str) -> None:
    node = parse(source)
    model = abstract(node, TYPES)
    if model is not None and is_unsatisfiable(model):
        assert not any(fires(node, r) for r in GRID), source


@settings(max_examples=250, deadline=None)
@given(_expressions(), _expressions())
def test_implication_claims_are_true(a: str, b: str) -> None:
    na, nb = parse(a), parse(b)
    ma, mb = abstract(na, TYPES), abstract(nb, TYPES)
    if ma is None or mb is None or not implies(ma, mb):
        return
    for record in GRID:
        if fires(na, record):
            assert fires(nb, record), (a, b, record)


@settings(max_examples=150, deadline=None)
@given(_expressions())
def test_every_expression_implies_itself_unless_opaque(source: str) -> None:
    node = parse(source)
    model = abstract(node, TYPES)
    assert model is not None
    if all(not t.opaque for t in model.terms):
        assert implies(model, model)


def test_properties_are_not_vacuous() -> None:
    """Seeded random pairs must yield many real implications and dead rules."""
    rng = random.Random(7)
    atoms = [
        "x > 1",
        "x > 2",
        "x >= 0",
        "x < -1",
        "y == 2",
        "y in [1, 2]",
        "s == 'a'",
        "s != 'b'",
        "ok",
    ]
    implications = unsat = 0
    for _ in range(400):
        a = " and ".join(rng.sample(atoms, rng.randint(1, 3)))
        b = " and ".join(rng.sample(atoms, rng.randint(1, 2)))
        ma, mb = abstract(parse(a), TYPES), abstract(parse(b), TYPES)
        assert ma is not None and mb is not None
        unsat += is_unsatisfiable(ma)
        if implies(ma, mb) and not is_unsatisfiable(ma):
            implications += 1
            na, nb = parse(a), parse(b)
            assert all(fires(nb, r) for r in GRID if fires(na, r))
    assert implications > 30 and unsat > 5
