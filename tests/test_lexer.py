from __future__ import annotations

import pytest

from rulelens.errors import ExpressionError
from rulelens.lexer import tokenize


def kinds(source: str) -> list[str]:
    return [t.kind for t in tokenize(source)]


def test_basic_tokens() -> None:
    assert kinds("a >= 10 and b") == ["IDENT", "OP", "NUMBER", "KEYWORD", "IDENT", "EOF"]


def test_numbers_keep_int_and_float_distinct() -> None:
    values = [t.value for t in tokenize("1 2.5 3e2 4.5E-1")[:-1]]
    assert values == [1, 2.5, 300.0, 0.45]
    assert isinstance(values[0], int)


@pytest.mark.parametrize(
    ("source", "expected"),
    [("'a\\'b'", "a'b"), ('"tab\\there"', "tab\there"), ("'back\\\\slash'", "back\\slash")],
)
def test_string_escapes(source: str, expected: str) -> None:
    assert tokenize(source)[0].value == expected


@pytest.mark.parametrize(
    ("source", "fragment"),
    [
        ("a = 1", "=="),
        ("a && b", "and"),
        ("a || b", "or"),
        ("!a", "not"),
        ("'open", "unterminated"),
        ("'bad\\q'", "escape"),
        ("12abc", "invalid number"),
        ("1e999", "out of range"),
        ("a $ b", "unexpected character"),
    ],
)
def test_lexical_errors_are_helpful(source: str, fragment: str) -> None:
    with pytest.raises(ExpressionError) as info:
        tokenize(source)
    assert fragment in str(info.value)


def test_error_render_points_at_the_problem() -> None:
    with pytest.raises(ExpressionError) as info:
        tokenize("a $ b")
    assert info.value.render().splitlines()[-1].strip() == "^"


def test_source_length_is_capped() -> None:
    with pytest.raises(ExpressionError, match="too long"):
        tokenize("1 + " * 600)


def test_token_positions() -> None:
    toks = tokenize("ab  <= 3")
    assert [(t.start, t.end) for t in toks[:3]] == [(0, 2), (4, 6), (7, 8)]
