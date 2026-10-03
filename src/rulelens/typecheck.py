"""Static type checking of rule expressions against a field schema.

Catching ``income < "high"`` or a misspelled field at load time, rather than
silently evaluating to unknown at decision time, is the main reason the
expression language is statically typed.

Types are plain strings: ``number``, ``string``, ``bool``, ``date``, ``null``,
and ``list[T]``. ``int`` schema fields are ``number`` here.
"""

from __future__ import annotations

import difflib
from collections.abc import Callable, Iterable

from .errors import ExpressionError
from .functions import ANY, FUNCTIONS
from .nodes import (
    ARITHMETIC_OPS,
    COMPARISON_OPS,
    Binary,
    Call,
    Field,
    IsNull,
    ListLiteral,
    Literal,
    Logical,
    Node,
    Unary,
)

FieldTypes = Callable[[str], str | None]


def typecheck(node: Node, source: str, field_types: FieldTypes, known: Iterable[str] = ()) -> str:
    """Return the static type of ``node`` or raise :class:`ExpressionError`.

    Args:
        node: the parsed expression.
        source: the expression text (used for error rendering).
        field_types: maps a dotted field name to its type, or ``None`` if unknown.
        known: all known field names, used for "did you mean" suggestions.
    """
    return _Checker(source, field_types, tuple(known)).check(node)


def element_type(list_type: str) -> str:
    return list_type[5:-1]


def _is_list(t: str) -> bool:
    return t.startswith("list[")


class _Checker:
    def __init__(self, source: str, field_types: FieldTypes, known: tuple[str, ...]) -> None:
        self.source = source
        self.field_types = field_types
        self.known = known

    def fail(self, message: str, node: Node, code: str = "E003") -> ExpressionError:
        return ExpressionError(message, self.source, node.span[0], node.span[1], code)

    def check(self, node: Node) -> str:
        if isinstance(node, Literal):
            return _literal_type(node.value)
        if isinstance(node, Field):
            found = self.field_types(node.name)
            if found is None:
                hint = difflib.get_close_matches(node.name, self.known, n=1)
                suffix = f"; did you mean '{hint[0]}'?" if hint else ""
                raise self.fail(f"unknown field '{node.name}'{suffix}", node, "E002")
            return found
        if isinstance(node, ListLiteral):
            return self._list(node)
        if isinstance(node, Unary):
            return self._unary(node)
        if isinstance(node, Logical):
            for side in (node.left, node.right):
                self._require(side, "bool", f"'{node.op}' needs boolean operands")
            return "bool"
        if isinstance(node, IsNull):
            self.check(node.operand)
            return "bool"
        if isinstance(node, Binary):
            return self._binary(node)
        if isinstance(node, Call):
            return self._call(node)
        raise TypeError(f"unknown node {node!r}")  # pragma: no cover

    def _require(self, node: Node, expected: str, message: str) -> None:
        actual = self.check(node)
        if actual not in (expected, "null"):
            raise self.fail(f"{message}, but this is {actual}", node)

    def _list(self, node: ListLiteral) -> str:
        types = {self.check(item) for item in node.items}
        if "null" in types or any(_is_list(t) for t in types):
            raise self.fail("list elements must be plain values (no null, no nested lists)", node)
        if len(types) > 1:
            raise self.fail(f"list elements must share one type, found {sorted(types)}", node)
        return f"list[{types.pop()}]" if types else "list[any]"

    def _unary(self, node: Unary) -> str:
        if node.op == "not":
            self._require(node.operand, "bool", "'not' needs a boolean operand")
            return "bool"
        self._require(node.operand, "number", "unary '-' needs a number")
        return "number"

    def _binary(self, node: Binary) -> str:
        op = node.op
        if op == "in":
            right = self.check(node.right)
            if not _is_list(right):
                raise self.fail("right-hand side of 'in' must be a list like [1, 2, 3]", node.right)
            left = self.check(node.left)
            elem = element_type(right)
            if elem != ANY and left not in (elem, "null"):
                raise self.fail(f"cannot test {left} against a list of {elem}", node)
            return "bool"
        left, right = self.check(node.left), self.check(node.right)
        if _is_list(left) or _is_list(right):
            raise self.fail("lists can only be used with 'in'", node)
        if op in ARITHMETIC_OPS:
            for side, t in ((node.left, left), (node.right, right)):
                if t not in ("number", "null"):
                    raise self.fail(f"'{op}' needs numbers, but this is {t}", side)
            return "number"
        assert op in COMPARISON_OPS
        for side in (node.left, node.right):
            if isinstance(side, Literal) and side.value is None:
                raise self.fail(
                    f"'{op} null' is always unknown; "
                    "use 'is null' or 'is not null' to test for missing values",
                    node,
                )
        if "null" not in (left, right) and left != right:
            raise self.fail(f"cannot compare {left} {op} {right}", node)
        if op not in ("==", "!=") and (left if left != "null" else right) in ("bool",):
            raise self.fail(f"cannot order booleans with '{op}'", node)
        return "bool"

    def _call(self, node: Call) -> str:
        spec = FUNCTIONS.get(node.name)
        if spec is None:
            hint = difflib.get_close_matches(node.name, FUNCTIONS, n=1)
            suffix = f"; did you mean '{hint[0]}'?" if hint else ""
            raise self.fail(f"unknown function '{node.name}'{suffix}", node, "E002")
        n_params = len(spec.params)
        low = n_params - spec.optional
        count = len(node.args)
        if count < low or (count > n_params and not spec.variadic):
            expected = f"{low}" if low == n_params else f"{low} to {n_params}"
            raise self.fail(f"{node.name}() takes {expected} argument(s), got {count}", node)
        arg_types = [self.check(a) for a in node.args]
        for i, (arg, actual) in enumerate(zip(node.args, arg_types, strict=True)):
            accepted = spec.params[min(i, n_params - 1)]
            if ANY not in accepted and actual not in accepted and actual != "null":
                names = " or ".join(sorted(accepted))
                raise self.fail(
                    f"{node.name}() argument {i + 1} must be {names}, got {actual}", arg
                )
        if spec.name == "between" and len({t for t in arg_types if t != "null"}) > 1:
            raise self.fail("between() arguments must all have the same type", node)
        if spec.returns == ANY:
            kinds = {t for t in arg_types if t != "null"}
            if len(kinds) > 1:
                raise self.fail(
                    f"{node.name}() arguments must share one type, found {sorted(kinds)}", node
                )
            return kinds.pop() if kinds else "null"
        return spec.returns


def _literal_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, str):
        return "string"
    return "number"
