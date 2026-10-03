"""Explanation trees: *why* a rule's condition came out the way it did."""

from __future__ import annotations

from dataclasses import dataclass

from .evaluator import Resolver, evaluate
from .nodes import Binary, Logical, Node, Unary, Value, referenced_fields


@dataclass(frozen=True, slots=True)
class Clause:
    """One node of an evaluated condition.

    Leaves (``op is None``) are comparisons or calls and carry the field values
    they read in ``bindings``; ``and`` / ``or`` / ``not`` nodes carry children.
    ``result`` is ``True``, ``False`` or ``None`` (unknown, due to missing data).
    """

    text: str
    result: bool | None
    op: str | None = None
    children: tuple[Clause, ...] = ()
    bindings: tuple[tuple[str, Value], ...] = ()

    def all_bindings(self) -> tuple[tuple[str, Value], ...]:
        """Field values consulted by this clause and everything beneath it."""
        found: dict[str, Value] = dict(self.bindings)
        for child in self.children:
            found.update(child.all_bindings())
        return tuple(found.items())

    def failing_leaves(self) -> list[Clause]:
        """Clauses that are not true and cannot be broken down further.

        These are what stopped a rule from firing. A ``not`` clause is treated as
        a unit so that its text reads naturally (``not employed``).
        """
        if self.op is None or self.op == "not":
            return [] if self.result is True else [self]
        out: list[Clause] = []
        for child in self.children:
            out.extend(child.failing_leaves())
        return out

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {"text": self.text, "result": self.result}
        if self.op is not None:
            data["op"] = self.op
            data["children"] = [c.to_dict() for c in self.children]
        else:
            data["bindings"] = {name: _jsonable(value) for name, value in self.bindings}
        return data


def _jsonable(value: Value) -> object:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    return value.isoformat()


def build_clause(node: Node, source: str, resolve: Resolver, warn: list[str]) -> Clause:
    """Evaluate ``node`` while recording every boolean sub-result.

    Unlike :func:`rulelens.evaluator.evaluate`, this never short-circuits, so the
    explanation shows every value that was consulted. The combined result is
    still computed with Kleene logic and equals what ``evaluate`` returns.
    """
    text = source[node.span[0] : node.span[1]]
    if isinstance(node, Logical):
        left = build_clause(node.left, source, resolve, warn)
        right = build_clause(node.right, source, resolve, warn)
        return Clause(text, _combine(node.op, left.result, right.result), node.op, (left, right))
    if isinstance(node, Unary) and node.op == "not" and not _is_membership(node.operand):
        inner = build_clause(node.operand, source, resolve, warn)
        result = None if inner.result is None else not inner.result
        return Clause(text, result, "not", (inner,))
    value = evaluate(node, resolve, warn)
    result = value if isinstance(value, bool) else None
    bindings = tuple((name, resolve(tuple(name.split(".")))) for name in referenced_fields(node))
    return Clause(text, result, None, (), bindings)


def _is_membership(node: Node) -> bool:
    return isinstance(node, Binary) and node.op == "in"


def _combine(op: str, left: bool | None, right: bool | None) -> bool | None:
    if op == "and":
        if left is False or right is False:
            return False
    elif left is True or right is True:
        return True
    return None if left is None or right is None else op == "and"
