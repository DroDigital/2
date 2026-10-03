from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rulelens.errors import ExpressionError
from rulelens.nodes import (
    Binary,
    Call,
    Field,
    IsNull,
    ListLiteral,
    Literal,
    Logical,
    Node,
    Unary,
    referenced_fields,
    to_source,
)
from rulelens.parser import parse


def test_precedence_of_arithmetic() -> None:
    assert parse("1 + 2 * 3") == Binary("+", Literal(1), Binary("*", Literal(2), Literal(3)))
    assert parse("(1 + 2) * 3") == Binary("*", Binary("+", Literal(1), Literal(2)), Literal(3))


def test_subtraction_is_left_associative() -> None:
    assert parse("10 - 3 - 2") == Binary("-", Binary("-", Literal(10), Literal(3)), Literal(2))


def test_logical_precedence_and_binds_tighter_than_or() -> None:
    assert parse("a or b and c") == Logical(
        "or", Field(("a",)), Logical("and", Field(("b",)), Field(("c",)))
    )


def test_not_binds_looser_than_comparison() -> None:
    assert parse("not a == 1") == Unary("not", Binary("==", Field(("a",)), Literal(1)))


def test_not_in_and_is_not_null() -> None:
    assert parse("a not in [1]") == Unary(
        "not", Binary("in", Field(("a",)), ListLiteral((Literal(1),)))
    )
    assert parse("a is not null") == IsNull(Field(("a",)), negated=True)
    assert parse("a is null") == IsNull(Field(("a",)), negated=False)


def test_negative_literals_are_folded() -> None:
    assert parse("-5") == Literal(-5)
    assert parse("- -5") == Literal(5)
    assert parse("-x") == Unary("-", Field(("x",)))
    assert parse("-true") == Unary("-", Literal(True))


def test_dotted_paths_and_calls() -> None:
    assert parse("applicant.address.zip") == Field(("applicant", "address", "zip"))
    assert parse("max(a, 2)") == Call("max", (Field(("a",)), Literal(2)))
    assert parse("f()") == Call("f", ())


def test_spans_cover_the_source_text() -> None:
    src = "income >= 5000 and (dti < 0.4)"
    node = parse(src)
    assert isinstance(node, Logical)
    assert src[slice(*node.left.span)] == "income >= 5000"
    assert src[slice(*node.right.span)] == "(dti < 0.4)"


def test_referenced_fields_in_first_use_order() -> None:
    assert referenced_fields(parse("b > 1 and a < 2 or b == 3")) == ("b", "a")


@pytest.mark.parametrize(
    ("source", "fragment"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("a < b < c", "chained"),
        ("a ==", "expected a value"),
        ("(a", "expected ')'"),
        ("a b", "unexpected 'b'"),
        ("a is 3", "expected 'null'"),
        ("[1, 2", "expected ']'"),
        ("a.", "field name after"),
        ("f(1,", "expected a value"),
        ("in", "unexpected keyword"),
        ("1 +", "expected a value"),
    ],
)
def test_syntax_errors(source: str, fragment: str) -> None:
    with pytest.raises(ExpressionError) as info:
        parse(source)
    assert fragment in str(info.value)


def test_nesting_depth_is_limited() -> None:
    with pytest.raises(ExpressionError, match="nested too deeply"):
        parse("(" * 200 + "1" + ")" * 200)


# -- property: printing then parsing returns the same tree --------------------------------------

_names = st.sampled_from(["a", "b", "income", "x1"])
_leaf: st.SearchStrategy[Node] = st.one_of(
    st.integers(0, 10**6).map(Literal),
    st.integers(-100, -1).map(Literal),
    st.floats(allow_nan=False, allow_infinity=False, width=32).map(Literal),
    st.text(alphabet=st.characters(codec="utf-8", exclude_categories=["Cs"]), max_size=6).map(
        Literal
    ),
    st.booleans().map(Literal),
    st.just(Literal(None)),
    _names.map(lambda n: Field((n,))),
    st.tuples(_names, _names).map(lambda p: Field(p)),
)


def _extend(children: st.SearchStrategy[Node]) -> st.SearchStrategy[Node]:
    def neg(n: Node) -> Node:
        # "-5" parses to Literal(-5), so a Unary minus over a numeric literal is not canonical.
        return Unary("-", n)

    return st.one_of(
        children.filter(
            lambda n: (
                not (
                    isinstance(n, Literal)
                    and isinstance(n.value, int | float)
                    and not isinstance(n.value, bool)
                )
            )
        ).map(neg),
        children.map(lambda n: Unary("not", n)),
        st.tuples(
            st.sampled_from(["+", "-", "*", "/", "%", "==", "!=", "<", "<=", ">", ">=", "in"]),
            children,
            children,
        ).map(lambda t: Binary(*t)),
        st.tuples(st.sampled_from(["and", "or"]), children, children).map(lambda t: Logical(*t)),
        st.tuples(children, st.booleans()).map(lambda t: IsNull(*t)),
        st.lists(children, max_size=3).map(lambda items: ListLiteral(tuple(items))),
        st.tuples(st.sampled_from(["max", "lower"]), st.lists(children, max_size=3)).map(
            lambda t: Call(t[0], tuple(t[1]))
        ),
    )


@given(st.recursive(_leaf, _extend, max_leaves=12))
def test_print_parse_roundtrip(tree: Node) -> None:
    assert parse(to_source(tree)) == tree
