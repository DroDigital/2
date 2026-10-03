"""Tokenizer for the RuleLens expression language."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Final

from .errors import ExpressionError

MAX_SOURCE_LENGTH: Final = 2000

KEYWORDS: Final = frozenset({"and", "or", "not", "in", "is", "null", "true", "false"})

_NUMBER = re.compile(r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TWO_CHAR_OPS: Final = ("==", "!=", "<=", ">=")
_ONE_CHAR_OPS: Final = "<>+-*/%"
_PUNCTUATION: Final = {
    "(": "LPAREN",
    ")": "RPAREN",
    "[": "LBRACKET",
    "]": "RBRACKET",
    ",": "COMMA",
    ".": "DOT",
}
_ESCAPES: Final = {"\\": "\\", "'": "'", '"': '"', "n": "\n", "t": "\t"}

_HINTS: Final = {
    "=": "use '==' to compare for equality",
    "&": "use 'and' instead of '&&'",
    "|": "use 'or' instead of '||'",
    "!": "use 'not' instead of '!' (and '!=' for inequality)",
}


@dataclass(frozen=True, slots=True)
class Token:
    kind: str
    text: str
    start: int
    end: int
    value: object = None


def tokenize(source: str) -> list[Token]:
    """Split ``source`` into tokens, ending with an ``EOF`` token."""
    if len(source) > MAX_SOURCE_LENGTH:
        raise ExpressionError(
            f"expression is too long ({len(source)} characters; limit is {MAX_SOURCE_LENGTH})",
            source[:80] + "...",
            0,
        )
    tokens: list[Token] = []
    pos = 0
    n = len(source)
    while pos < n:
        ch = source[pos]
        if ch.isspace():
            pos += 1
            continue
        if ch.isdigit():
            pos = _read_number(source, pos, tokens)
        elif ch in "\"'":
            pos = _read_string(source, pos, tokens)
        elif ch.isalpha() or ch == "_":
            match = _IDENT.match(source, pos)
            assert match is not None
            text = match.group()
            kind = "KEYWORD" if text in KEYWORDS else "IDENT"
            tokens.append(Token(kind, text, pos, match.end()))
            pos = match.end()
        elif source.startswith(_TWO_CHAR_OPS, pos):
            tokens.append(Token("OP", source[pos : pos + 2], pos, pos + 2))
            pos += 2
        elif ch in _ONE_CHAR_OPS:
            tokens.append(Token("OP", ch, pos, pos + 1))
            pos += 1
        elif ch in _PUNCTUATION:
            tokens.append(Token(_PUNCTUATION[ch], ch, pos, pos + 1))
            pos += 1
        else:
            hint = _HINTS.get(ch)
            suffix = f" ({hint})" if hint else ""
            raise ExpressionError(f"unexpected character {ch!r}{suffix}", source, pos)
    tokens.append(Token("EOF", "", n, n))
    return tokens


def _read_number(source: str, pos: int, tokens: list[Token]) -> int:
    match = _NUMBER.match(source, pos)
    assert match is not None
    text = match.group()
    end = match.end()
    if end < len(source) and (source[end].isalpha() or source[end] == "_"):
        raise ExpressionError(f"invalid number {source[pos : end + 1]!r}", source, pos, end + 1)
    is_float = any(c in text for c in ".eE")
    value = float(text) if is_float else int(text)
    if isinstance(value, float) and not math.isfinite(value):
        raise ExpressionError(f"number {text!r} is out of range", source, pos, end)
    tokens.append(Token("NUMBER", text, pos, end, value))
    return end


def _read_string(source: str, pos: int, tokens: list[Token]) -> int:
    quote = source[pos]
    chars: list[str] = []
    i = pos + 1
    while i < len(source):
        c = source[i]
        if c == quote:
            tokens.append(Token("STRING", source[pos : i + 1], pos, i + 1, "".join(chars)))
            return i + 1
        if c == "\\":
            if i + 1 >= len(source) or source[i + 1] not in _ESCAPES:
                raise ExpressionError("invalid escape sequence in string", source, i, i + 2)
            chars.append(_ESCAPES[source[i + 1]])
            i += 2
            continue
        chars.append(c)
        i += 1
    raise ExpressionError("unterminated string literal", source, pos, len(source))
