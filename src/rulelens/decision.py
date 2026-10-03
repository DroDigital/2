"""The result of evaluating one record against a policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .nodes import Value
from .trace import Clause, _jsonable


@dataclass(frozen=True, slots=True)
class PolicyRef:
    """Identifies exactly which policy produced a decision (for audit trails)."""

    name: str
    version: str
    fingerprint: str

    def __str__(self) -> str:
        return f"{self.name} v{self.version} [{self.fingerprint}]"


@dataclass(frozen=True, slots=True)
class RuleResult:
    rule_id: str
    outcome: str
    result: bool | None  # True = fired, False = did not fire, None = could not be evaluated
    clause: Clause | None = None  # present when the decision was made with trace=True
    missing: tuple[str, ...] = ()  # input fields that were unknown, when result is None

    @property
    def fired(self) -> bool:
        return self.result is True


@dataclass(frozen=True, slots=True)
class Decision:
    """An outcome together with everything needed to justify it.

    Attributes:
        outcome: the final decision, one of the policy's outcomes.
        decisive: ids of the rules that determine the outcome.
        reasons: rendered, human-readable justifications.
        defaulted: ``True`` when no rule fired and the policy default applied.
        escalated: ``True`` when missing data pushed the outcome to ``on_unknown``.
        rule_results: per-rule results, in policy order.
        facts: the typed input values plus derived fields that rules saw.
        warnings: data problems encountered (bad values, division by zero, ...).
        policy: which policy version decided.
    """

    outcome: str
    decisive: tuple[str, ...]
    reasons: tuple[str, ...]
    defaulted: bool
    escalated: bool
    rule_results: tuple[RuleResult, ...]
    facts: dict[str, Value] = field(repr=False)
    warnings: tuple[str, ...]
    policy: PolicyRef

    @property
    def fired(self) -> tuple[str, ...]:
        return tuple(r.rule_id for r in self.rule_results if r.result is True)

    @property
    def unknown(self) -> tuple[str, ...]:
        return tuple(r.rule_id for r in self.rule_results if r.result is None)

    @property
    def missing_fields(self) -> tuple[str, ...]:
        """Input fields whose absence stopped at least one rule from being evaluated."""
        found: dict[str, None] = {}
        for r in self.rule_results:
            for name in r.missing:
                found.setdefault(name, None)
        return tuple(found)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form, suitable for storing in an audit log."""
        return {
            "outcome": self.outcome,
            "decisive": list(self.decisive),
            "reasons": list(self.reasons),
            "defaulted": self.defaulted,
            "escalated": self.escalated,
            "fired": list(self.fired),
            "unknown": list(self.unknown),
            "missing_fields": list(self.missing_fields),
            "warnings": list(self.warnings),
            "policy": {
                "name": self.policy.name,
                "version": self.policy.version,
                "fingerprint": self.policy.fingerprint,
            },
            "facts": {k: _jsonable(v) for k, v in self.facts.items()},
            "rules": [
                {
                    "id": r.rule_id,
                    "outcome": r.outcome,
                    "result": r.result,
                    **({"trace": r.clause.to_dict()} if r.clause is not None else {}),
                }
                for r in self.rule_results
            ],
        }

    def explain(self, *, verbose: bool = True) -> str:
        """Multi-line, human-readable justification of the decision."""
        lines = [f"Decision: {self.outcome.upper()}   ({self.policy})"]
        for reason in self.reasons:
            lines.append(f"  • {reason}")
        if self.missing_fields:
            lines.append(f"  Missing data: {', '.join(self.missing_fields)}")
        if verbose:
            lines.extend(self._rule_lines())
        for warning in self.warnings:
            lines.append(f"  ! {warning}")
        return "\n".join(lines)

    def _rule_lines(self) -> list[str]:
        lines = ["", "  Rules"]
        for r in self.rule_results:
            mark = {True: "✔", False: "·", None: "?"}[r.result]
            suffix = "  (decisive)" if r.rule_id in self.decisive else ""
            lines.append(f"    {mark} {r.rule_id} → {r.outcome}{suffix}")
            if r.clause is not None and (r.result is True or r.result is None):
                lines.extend(_clause_lines(r.clause, 6))
            elif r.clause is not None:
                for leaf in r.clause.failing_leaves():
                    lines.append(f"{' ' * 6}{format_clause(leaf)}")
        return lines


def format_clause(clause: Clause) -> str:
    mark = {True: "true", False: "false", None: "unknown"}[clause.result]
    bindings = clause.all_bindings()
    values = ", ".join(f"{name}={display(value, quote=True)}" for name, value in bindings)
    detail = f"  [{values}]" if values else ""
    return f"{clause.text} → {mark}{detail}"


def _clause_lines(clause: Clause, indent: int) -> list[str]:
    pad = " " * indent
    if clause.op is None:
        return [pad + format_clause(clause)]
    if clause.op == "not" or not clause.children:
        return [pad + format_clause(clause)]
    lines: list[str] = []
    for child in clause.children:
        lines.extend(_clause_lines(child, indent))
    return lines


def display(value: Value, *, quote: bool = False) -> str:
    """Render a value for people: dates as ISO, ``None`` as ``missing``."""
    if value is None:
        return "missing"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return f'"{value}"' if quote else value
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, tuple):
        return "[" + ", ".join(display(v, quote=True) for v in value) + "]"
    return str(value)
