"""Abstract syntax tree for rule expressions, plus a canonical printer.

Nodes compare structurally; the source ``span`` is excluded from equality so
that ``parse(to_source(parse(s))) == parse(s)`` holds.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from typing import Final

Scalar = None | bool | int | float | str
Span = tuple[int, int]

# Binding power of each construct; higher binds tighter. Shared by parser and printer.
PREC_OR: Final = 1
PREC_AND: Final = 2
PREC_NOT: Final = 3
PREC_COMPARE: Final = 4
PREC_ADD: Final = 5
PREC_MUL: Final = 6
PREC_UNARY: Final = 7
PREC_ATOM: Final = 8

COMPARISON_OPS: Final = frozenset({"==", "!=", "<", "<=", ">", ">="})
ARITHMETIC_OPS: Final = frozenset({"+", "-", "*", "/", "%"})


@dataclass(frozen=True, slots=True)
class Literal:
    value: Scalar
    span: Span = field(default=(0, 0), compare=False, repr=False, kw_only=True)


@dataclass(frozen=True, slots=True)
class ListLiteral:
    items: tuple[Node, ...]
    span: Span = field(default=(0, 0), compare=False, repr=False, kw_only=True)


@dataclass(frozen=True, slots=True)
class Field:
    path: tuple[str, ...]
    span: Span = field(default=(0, 0), compare=False, repr=False, kw_only=True)

    @property
    def name(self) -> str:
        return ".".join(self.path)


@dataclass(frozen=True, slots=True)
class Unary:
    op: str  # "-" or "not"
    operand: Node
    span: Span = field(default=(0, 0), compare=False, repr=False, kw_only=True)


@dataclass(frozen=True, slots=True)
class Binary:
    op: str  # arithmetic, comparison, or "in"
    left: Node
    right: Node
    span: Span = field(default=(0, 0), compare=False, repr=False, kw_only=True)


@dataclass(frozen=True, slots=True)
class Logical:
    op: str  # "and" or "or"
    left: Node
    right: Node
    span: Span = field(default=(0, 0), compare=False, repr=False, kw_only=True)


@dataclass(frozen=True, slots=True)
class IsNull:
    operand: Node
    negated: bool = False
    span: Span = field(default=(0, 0), compare=False, repr=False, kw_only=True)


@dataclass(frozen=True, slots=True)
class Call:
    name: str
    args: tuple[Node, ...]
    span: Span = field(default=(0, 0), compare=False, repr=False, kw_only=True)


Node = Literal | ListLiteral | Field | Unary | Binary | Logical | IsNull | Call

# Runtime value domain of the evaluator.
Value = None | bool | int | float | str | date | tuple["Value", ...]

_BINARY_PREC: Final = {
    "or": PREC_OR,
    "and": PREC_AND,
    "==": PREC_COMPARE,
    "!=": PREC_COMPARE,
    "<": PREC_COMPARE,
    "<=": PREC_COMPARE,
    ">": PREC_COMPARE,
    ">=": PREC_COMPARE,
    "in": PREC_COMPARE,
    "+": PREC_ADD,
    "-": PREC_ADD,
    "*": PREC_MUL,
    "/": PREC_MUL,
    "%": PREC_MUL,
}


def children(node: Node) -> tuple[Node, ...]:
    """Direct sub-expressions of ``node``."""
    match node:
        case Literal() | Field():
            return ()
        case ListLiteral(items=items):
            return items
        case Unary(operand=operand) | IsNull(operand=operand):
            return (operand,)
        case Binary(left=left, right=right) | Logical(left=left, right=right):
            return (left, right)
        case Call(args=args):
            return args
    raise TypeError(f"unknown node {node!r}")  # pragma: no cover


def walk(node: Node) -> Iterator[Node]:
    """Yield ``node`` and all of its descendants, depth-first."""
    yield node
    for child in children(node):
        yield from walk(child)


def referenced_fields(node: Node) -> tuple[str, ...]:
    """Dotted names of every field the expression reads, in first-use order."""
    seen: dict[str, None] = {}
    for sub in walk(node):
        if isinstance(sub, Field):
            seen.setdefault(sub.name, None)
    return tuple(seen)


def format_literal(value: Scalar) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        escaped = (
            value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\t", "\\t")
        )
        return f'"{escaped}"'
    return repr(value)


def to_source(node: Node) -> str:
    """Canonical text for ``node`` that re-parses to an equal tree."""
    return _print(node, 0)


def _print(node: Node, parent_prec: int) -> str:
    text, prec = _print_with_prec(node)
    return f"({text})" if prec < parent_prec else text


def _print_with_prec(node: Node) -> tuple[str, int]:
    match node:
        case Literal(value=value):
            return format_literal(value), PREC_ATOM
        case Field():
            return node.name, PREC_ATOM
        case ListLiteral(items=items):
            return "[" + ", ".join(_print(i, 0) for i in items) + "]", PREC_ATOM
        case Call(name=name, args=args):
            return f"{name}(" + ", ".join(_print(a, 0) for a in args) + ")", PREC_ATOM
        case Unary(op="not", operand=Binary(op="in", left=left, right=right)):
            return f"{_print(left, PREC_ADD)} not in {_print(right, PREC_ADD)}", PREC_COMPARE
        case Unary(op="not", operand=operand):
            return f"not {_print(operand, PREC_NOT)}", PREC_NOT
        case Unary(op=op, operand=operand):
            return f"{op}{_print(operand, PREC_UNARY)}", PREC_UNARY
        case IsNull(operand=operand, negated=negated):
            suffix = "is not null" if negated else "is null"
            return f"{_print(operand, PREC_ADD)} {suffix}", PREC_COMPARE
        case Logical(op=op, left=left, right=right):
            prec = _BINARY_PREC[op]
            # Left-associative: the right operand needs parentheses at equal precedence.
            return f"{_print(left, prec)} {op} {_print(right, prec + 1)}", prec
        case Binary(op=op, left=left, right=right):
            prec = _BINARY_PREC[op]
            if prec == PREC_COMPARE:
                # Comparisons do not chain, so both sides must bind tighter.
                return f"{_print(left, prec + 1)} {op} {_print(right, prec + 1)}", prec
            return f"{_print(left, prec)} {op} {_print(right, prec + 1)}", prec
    raise TypeError(f"unknown node {node!r}")  # pragma: no cover
