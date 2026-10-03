"""Static analysis of policies: find rules that can never matter.

Every rule condition is abstracted into a *union of boxes*: each box (a
:class:`Term`) constrains some fields to an interval set (numbers) or a value
set (strings, booleans), and a rule matches when all constraints of any one box
hold. From that abstraction we can prove, without any data:

* **unsatisfiable** rules (``age > 65 and age < 18``);
* **shadowed** rules under ``first_match`` (an earlier rule always matches first);
* **redundant** rules under ``most_severe`` (a more severe rule always matches too).

The analysis is *sound but incomplete*: a finding is always true, but some real
problems go unreported. Anything the abstraction cannot express (arithmetic
between fields, function calls, ``is null``, dates) is treated as an opaque
condition that may or may not hold, which can only hide findings, never invent
them. ``tests/test_analysis_properties.py`` checks these claims against the
evaluator on thousands of generated policies and records.

Missing data does not weaken the argument. Three-valued logic is a De Morgan
algebra, so pushing ``not`` down to the atoms is exact, and an atom is only ever
true for a non-null value, which every containment check requires explicitly.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from .diagnostics import INFO, WARNING, Diagnostic
from .nodes import (
    COMPARISON_OPS,
    Binary,
    Field,
    ListLiteral,
    Literal,
    Logical,
    Node,
    Unary,
    referenced_fields,
)
from .policy import Policy, Rule, template_fields

MAX_TERMS: Final = 64  # give up (stay silent) rather than blow up on huge disjunctions


# -- interval sets over the reals ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Interval:
    lo: float
    lo_closed: bool
    hi: float
    hi_closed: bool

    def is_empty(self) -> bool:
        return self.lo > self.hi or (self.lo == self.hi and not (self.lo_closed and self.hi_closed))


def _touches(a: Interval, b: Interval) -> bool:
    """``b`` starts no earlier than ``a`` and overlaps or abuts it."""
    return b.lo < a.hi or (b.lo == a.hi and (a.hi_closed or b.lo_closed))


def _normalize(intervals: Iterable[Interval]) -> tuple[Interval, ...]:
    live = sorted((i for i in intervals if not i.is_empty()), key=lambda i: (i.lo, not i.lo_closed))
    out: list[Interval] = []
    for iv in live:
        if out and _touches(out[-1], iv):
            last = out[-1]
            if iv.hi > last.hi or (iv.hi == last.hi and iv.hi_closed):
                hi, hi_closed = iv.hi, iv.hi_closed
            else:
                hi, hi_closed = last.hi, last.hi_closed
            out[-1] = Interval(last.lo, last.lo_closed, hi, hi_closed)
        else:
            out.append(iv)
    return tuple(out)


@dataclass(frozen=True, slots=True)
class Numeric:
    """A set of real numbers as disjoint, sorted intervals."""

    intervals: tuple[Interval, ...]

    @classmethod
    def of(cls, intervals: Iterable[Interval]) -> Numeric:
        return cls(_normalize(intervals))

    def intersect(self, other: Numeric) -> Numeric:
        parts = []
        for a in self.intervals:
            for b in other.intervals:
                lo, lo_closed = max((a.lo, a.lo_closed), (b.lo, b.lo_closed), key=_lo_key)
                hi, hi_closed = min((a.hi, a.hi_closed), (b.hi, b.hi_closed), key=_hi_key)
                parts.append(Interval(lo, lo_closed, hi, hi_closed))
        return Numeric.of(parts)

    def complement(self) -> Numeric:
        gaps: list[Interval] = []
        cur, cur_closed = -math.inf, False
        for iv in self.intervals:
            gaps.append(Interval(cur, cur_closed, iv.lo, not iv.lo_closed))
            cur, cur_closed = iv.hi, not iv.hi_closed
        gaps.append(Interval(cur, cur_closed, math.inf, False))
        return Numeric.of(gaps)

    def is_empty(self) -> bool:
        return not self.intervals

    def subset_of(self, other: Numeric) -> bool:
        return self.intersect(other.complement()).is_empty()


def _lo_key(end: tuple[float, bool]) -> tuple[float, bool]:
    return (end[0], end[1] is False)  # at equal lo, the open end is the tighter (larger) bound


def _hi_key(end: tuple[float, bool]) -> tuple[float, bool]:
    return (end[0], end[1] is True)  # at equal hi, the closed end is the looser (larger) bound


def _numeric_atom(op: str, value: float) -> Numeric:
    inf = math.inf
    shapes = {
        "<": [Interval(-inf, False, value, False)],
        "<=": [Interval(-inf, False, value, True)],
        ">": [Interval(value, False, inf, False)],
        ">=": [Interval(value, True, inf, False)],
        "==": [Interval(value, True, value, True)],
    }
    if op == "!=":
        return Numeric.of(shapes["=="]).complement()
    return Numeric.of(shapes[op])


# -- value sets for strings and booleans --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Cat:
    """Either exactly ``values`` or (if ``negated``) every value except ``values``.

    ``universe`` is the full set when it is finite (booleans); a negated set over
    a finite universe is rewritten as the equivalent plain set.
    """

    values: frozenset[Any]
    negated: bool = False
    universe: frozenset[Any] | None = None

    @classmethod
    def of(cls, values: Iterable[Any], negated: bool, universe: frozenset[Any] | None) -> Cat:
        vals = frozenset(values)
        if negated and universe is not None:
            return cls(universe - vals, False, universe)
        return cls(vals, negated, universe)

    def complement(self) -> Cat:
        return Cat.of(self.values, not self.negated, self.universe)

    def intersect(self, other: Cat) -> Cat:
        if not self.negated and not other.negated:
            return Cat(self.values & other.values, False, self.universe)
        if not self.negated:
            return Cat(self.values - other.values, False, self.universe)
        if not other.negated:
            return Cat(other.values - self.values, False, other.universe)
        return Cat.of(self.values | other.values, True, self.universe)

    def is_empty(self) -> bool:
        return not self.negated and not self.values

    def subset_of(self, other: Cat) -> bool:
        if not self.negated:
            return (
                self.values <= other.values
                if not other.negated
                else not (self.values & other.values)
            )
        if other.negated:
            return other.values <= self.values
        return False  # a cofinite set never fits inside a finite one (universe is infinite here)


Domain = Numeric | Cat

_BOOLS: Final = frozenset({True, False})


# -- boxes -------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Term:
    """One conjunction: all ``constraints`` hold (and, if ``opaque``, something unmodelled too)."""

    constraints: Mapping[str, Domain]
    opaque: bool = False


@dataclass(frozen=True, slots=True)
class Model:
    """A rule condition as a union of satisfiable terms. No terms means it can never fire."""

    terms: tuple[Term, ...]
    contradictions: tuple[str, ...] = ()


class _TooBig(Exception):
    pass


class _Abstractor:
    def __init__(self, types: Mapping[str, str]) -> None:
        self.types = types
        self.contradictions: dict[str, None] = {}

    def run(self, node: Node) -> Model | None:
        try:
            terms = self.dnf(node, False)
        except _TooBig:
            return None
        return Model(tuple(terms), tuple(self.contradictions) if not terms else ())

    def dnf(self, node: Node, negate: bool) -> list[Term]:
        if isinstance(node, Logical):
            conjunction = (node.op == "and") != negate  # De Morgan flips the connective
            left, right = self.dnf(node.left, negate), self.dnf(node.right, negate)
            return self._and(left, right) if conjunction else self._or(left, right)
        if isinstance(node, Unary) and node.op == "not":
            return self.dnf(node.operand, not negate)
        if isinstance(node, Literal) and isinstance(node.value, bool):
            return [Term({})] if node.value != negate else []
        atom = self._atom(node)
        if atom is None:
            return [Term({}, opaque=True)]
        name, domain = atom
        if negate:
            domain = domain.complement()
        return [] if domain.is_empty() else [Term({name: domain})]

    def _or(self, left: list[Term], right: list[Term]) -> list[Term]:
        merged = left + right
        if len(merged) > MAX_TERMS:
            raise _TooBig
        return merged

    def _and(self, left: list[Term], right: list[Term]) -> list[Term]:
        out: list[Term] = []
        for a in left:
            for b in right:
                term = self._merge(a, b)
                if term is not None:
                    out.append(term)
                if len(out) > MAX_TERMS:
                    raise _TooBig
        return out

    def _merge(self, a: Term, b: Term) -> Term | None:
        merged: dict[str, Domain] = dict(a.constraints)
        for name, dom in b.constraints.items():
            if name in merged:
                inter = merged[name].intersect(dom)  # type: ignore[arg-type]
                if inter.is_empty():
                    self.contradictions.setdefault(name, None)
                    return None
                merged[name] = inter
            else:
                merged[name] = dom
        return Term(merged, a.opaque or b.opaque)

    # -- leaves ---------------------------------------------------------------------------
    def _atom(self, node: Node) -> tuple[str, Domain] | None:
        if isinstance(node, Field) and self.types.get(node.name) == "bool":
            return node.name, Cat(frozenset({True}), False, _BOOLS)
        if not isinstance(node, Binary):
            return None
        if node.op == "in":
            return self._membership(node)
        if node.op in COMPARISON_OPS:
            return self._comparison(node)
        return None

    def _membership(self, node: Binary) -> tuple[str, Domain] | None:
        if not isinstance(node.left, Field) or not isinstance(node.right, ListLiteral):
            return None
        if not all(isinstance(i, Literal) for i in node.right.items):
            return None
        values = [i.value for i in node.right.items if isinstance(i, Literal)]
        name = node.left.name
        kind = self.types.get(name)
        if kind == "number" and all(_is_num(v) for v in values):
            points = [_numeric_atom("==", v) for v in values]  # type: ignore[arg-type]
            union = Numeric.of(iv for p in points for iv in p.intervals)
            return name, union
        if kind in ("string", "bool") and all(_matches(kind, v) for v in values):
            return name, self._cat(kind, values, negated=False)
        return None

    def _comparison(self, node: Binary) -> tuple[str, Domain] | None:
        op, left, right = node.op, node.left, node.right
        if isinstance(left, Literal) and isinstance(right, Field):
            left, right = right, left
            op = {"<": ">", "<=": ">=", ">": "<", ">=": "<="}.get(op, op)
        if not (isinstance(left, Field) and isinstance(right, Literal)):
            return None
        name, value, kind = left.name, right.value, self.types.get(left.name)
        if kind == "number" and _is_num(value):
            return name, _numeric_atom(op, value)  # type: ignore[arg-type]
        if kind in ("string", "bool") and op in ("==", "!=") and _matches(kind, value):
            return name, self._cat(kind, [value], negated=(op == "!="))
        return None

    @staticmethod
    def _cat(kind: str, values: list[Any], negated: bool) -> Cat:
        return Cat.of(values, negated, _BOOLS if kind == "bool" else None)


def _is_num(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _matches(kind: str, value: object) -> bool:
    return isinstance(value, bool) if kind == "bool" else isinstance(value, str)


# -- public queries ----------------------------------------------------------------------------


def field_types(policy: Policy) -> dict[str, str]:
    """Static type of every schema and derived field."""
    from .schema import static_type

    types = {name: static_type(t) for name, t in policy.schema.items()}
    types.update({d.name: d.type for d in policy.derived})
    return types


def abstract(node: Node, types: Mapping[str, str]) -> Model | None:
    """Abstract ``node`` into a :class:`Model`, or ``None`` if it is too large to analyse."""
    return _Abstractor(types).run(node)


def is_unsatisfiable(model: Model) -> bool:
    """``True`` if the condition can never be true for any record."""
    return not model.terms


def implies(a: Model, b: Model) -> bool:
    """``True`` only if every record matching ``a`` is certain to match ``b`` as well.

    Sufficient, not necessary: it may answer ``False`` for a true implication
    (for instance when ``b`` is covered only by the *union* of several terms).
    """
    return all(any(_term_implies(ta, tb) for tb in b.terms) for ta in a.terms)


def _term_implies(a: Term, b: Term) -> bool:
    if b.opaque:
        return False  # b demands something we cannot see
    for name, dom_b in b.constraints.items():
        dom_a = a.constraints.get(name)
        # Requiring ``name`` in a's box also forces it to be non-null, which b's atom needs.
        if dom_a is None or not dom_a.subset_of(dom_b):  # type: ignore[arg-type]
            return False
    return True


# -- lint --------------------------------------------------------------------------------------


def lint(policy: Policy) -> list[Diagnostic]:
    """Static findings for a loaded policy, most important first."""
    types = field_types(policy)
    models: dict[str, Model | None] = {r.id: abstract(r.expr, types) for r in policy.rules}
    findings: list[Diagnostic] = []

    for index, rule in enumerate(policy.rules):
        model = models[rule.id]
        if model is None:
            continue
        loc = f"rules[{rule.id}].when"
        if is_unsatisfiable(model):
            fields = ", ".join(f"'{f}'" for f in model.contradictions)
            detail = f" (the conditions on {fields} contradict each other)" if fields else ""
            findings.append(
                Diagnostic(WARNING, "W101", f"rule can never fire{detail}", rule.id, loc)
            )
            continue
        finding = _shadow_or_redundancy(policy, index, rule, models)
        if finding is not None:
            findings.append(finding)

    findings.extend(_unused(policy))
    if not policy.tests:
        findings.append(
            Diagnostic(
                INFO, "I203", "policy has no embedded [[tests]]; add some so changes are checked"
            )
        )
    return sorted(findings, key=Diagnostic.sort_key)


def _shadow_or_redundancy(
    policy: Policy, index: int, rule: Rule, models: Mapping[str, Model | None]
) -> Diagnostic | None:
    model = models[rule.id]
    assert model is not None
    loc = f"rules[{rule.id}].when"
    if policy.strategy == "first_match":
        for earlier in policy.rules[:index]:
            other = models[earlier.id]
            if other is not None and implies(model, other):
                return Diagnostic(
                    WARNING,
                    "W102",
                    f"rule is shadowed by earlier rule '{earlier.id}': whenever this rule "
                    "matches, that one matches first, so this rule never decides an outcome",
                    rule.id,
                    loc,
                )
        return None
    for other_rule in policy.rules:
        other = models[other_rule.id]
        if other_rule.id == rule.id or other is None:
            continue
        if policy.severity(other_rule.outcome) > policy.severity(rule.outcome) and implies(
            model, other
        ):
            return Diagnostic(
                WARNING,
                "W103",
                f"rule is redundant: whenever it fires, rule '{other_rule.id}' also fires with a "
                f"more severe outcome ('{other_rule.outcome}' over '{rule.outcome}'), "
                "so this rule is never decisive",
                rule.id,
                loc,
            )
    return None


def _unused(policy: Policy) -> list[Diagnostic]:
    used: set[str] = set()
    for rule in policy.rules:
        used.update(referenced_fields(rule.expr))
        used.update(name for name, _ in template_fields(rule.reason))
    for d in policy.derived:
        used.update(referenced_fields(d.expr))
    out = [
        Diagnostic(
            INFO, "I201", f"schema field '{name}' is not used by any rule", None, f"schema.{name}"
        )
        for name in policy.schema
        if name not in used
    ]
    out.extend(
        Diagnostic(
            INFO,
            "I202",
            f"derived field '{d.name}' is not used by any rule",
            None,
            f"derived.{d.name}",
        )
        for d in policy.derived
        if d.name not in used
    )
    return out
