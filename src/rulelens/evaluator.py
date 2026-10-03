"""Evaluation of expression trees with three-valued (Kleene) logic.

Missing data is a first-class concept. A field that is absent evaluates to
``None`` ("unknown"), and unknown propagates the way it does in SQL:

* any comparison or arithmetic involving unknown is unknown;
* ``false and unknown`` is ``false`` and ``true or unknown`` is ``true``;
* a rule fires only when its condition is *definitely* true.

The evaluator never raises on bad data. Type mismatches and division by zero
produce ``None`` and append a message to the supplied warning list.
"""

from __future__ import annotations

import operator
from collections.abc import Callable
from datetime import date
from typing import Final, TypeGuard

from .functions import FUNCTIONS
from .nodes import (
    Binary,
    Call,
    Field,
    IsNull,
    ListLiteral,
    Literal,
    Logical,
    Node,
    Unary,
    Value,
)

Resolver = Callable[[tuple[str, ...]], Value]

_ORDER_OPS: Final = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}
_ARITH_OPS: Final = {"+": operator.add, "-": operator.sub, "*": operator.mul}
_ORDERABLE: Final = frozenset({"number", "string", "date"})


def kind_of(value: Value) -> str:
    """Runtime kind of a value: null, bool, number, string, date, or list."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, date):
        return "date"
    return "list"


def evaluate(node: Node, resolve: Resolver, warn: list[str] | None = None) -> Value:
    """Evaluate ``node``; unknown results are returned as ``None``."""
    return _eval(node, resolve, warn if warn is not None else [])


def _eval(node: Node, resolve: Resolver, warn: list[str]) -> Value:
    if isinstance(node, Binary):
        return _binary(node, resolve, warn)
    if isinstance(node, Field):
        return resolve(node.path)
    if isinstance(node, Literal):
        return node.value
    if isinstance(node, Logical):
        left = _truth(_eval(node.left, resolve, warn), node, warn)
        # Short-circuiting is safe: evaluation is pure, so skipped work cannot change the result.
        if node.op == "and":
            if left is False:
                return False
            right = _truth(_eval(node.right, resolve, warn), node, warn)
            if right is False:
                return False
            return None if left is None or right is None else True
        if left is True:
            return True
        right = _truth(_eval(node.right, resolve, warn), node, warn)
        if right is True:
            return True
        return None if left is None or right is None else False
    if isinstance(node, Unary):
        value = _eval(node.operand, resolve, warn)
        if node.op == "not":
            truth = _truth(value, node, warn)
            return None if truth is None else not truth
        if _is_number(value):
            return -value
        return _mismatch(warn, f"cannot negate {kind_of(value)}")
    if isinstance(node, IsNull):
        is_null = _eval(node.operand, resolve, warn) is None
        return not is_null if node.negated else is_null
    if isinstance(node, Call):
        return _call(node, resolve, warn)
    if isinstance(node, ListLiteral):
        return tuple(_eval(item, resolve, warn) for item in node.items)
    raise TypeError(f"unknown node {node!r}")  # pragma: no cover


def _is_number(value: Value) -> TypeGuard[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _mismatch(warn: list[str], message: str) -> Value:
    """Record a type problem and return unknown."""
    warn.append(message)
    return None


def _truth(value: Value, node: Node, warn: list[str]) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    warn.append(f"expected a boolean but got {kind_of(value)}")
    return None


def _binary(node: Binary, resolve: Resolver, warn: list[str]) -> Value:
    op = node.op
    left = _eval(node.left, resolve, warn)
    right = _eval(node.right, resolve, warn)
    if left is None or right is None:
        return None

    if op == "in":
        if not isinstance(right, tuple):
            return _mismatch(warn, "right-hand side of 'in' must be a list")
        return any(_equal(left, item) for item in right)
    if op == "==":
        return _equal(left, right)
    if op == "!=":
        return not _equal(left, right)

    lk, rk = kind_of(left), kind_of(right)
    if op in _ORDER_OPS:
        if lk != rk or lk not in _ORDERABLE:
            return _mismatch(warn, f"cannot order {lk} {op} {rk}")
        return bool(_ORDER_OPS[op](left, right))

    if not (_is_number(left) and _is_number(right)):
        return _mismatch(warn, f"arithmetic '{op}' needs numbers, got {lk} and {rk}")
    if op in _ARITH_OPS:
        result: Value = _ARITH_OPS[op](left, right)
        return result
    if right == 0:
        return _mismatch(warn, f"division by zero in '{op}'")
    return left / right if op == "/" else left % right


def _equal(a: Value, b: Value) -> bool:
    # Values of different kinds are simply unequal (so 1 != true, 1 != "1").
    return kind_of(a) == kind_of(b) and a == b


def _call(node: Call, resolve: Resolver, warn: list[str]) -> Value:
    spec = FUNCTIONS[node.name]
    args = [_eval(arg, resolve, warn) for arg in node.args]
    if spec.null_propagating and any(a is None for a in args):
        return None
    try:
        result: Value = spec.impl(*args)
    except (TypeError, ValueError, OverflowError) as exc:
        return _mismatch(warn, f"{node.name}() failed: {exc}")
    if result is None and node.name == "date":
        warn.append(f"date(): {args[0]!r} is not an ISO date (YYYY-MM-DD)")
    return result
