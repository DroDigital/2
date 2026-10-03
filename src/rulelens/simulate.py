"""Replay records through a policy to see how it behaves in aggregate."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from .decision import Decision, PolicyRef
from .policy import Policy
from .schema import lookup

FOUR_FIFTHS: Final = 0.8
MAX_GROUPS: Final = 20
MISSING_GROUP: Final = "(missing)"
OTHER_GROUP: Final = "(other)"


@dataclass(frozen=True, slots=True)
class RuleStats:
    rule_id: str
    outcome: str
    fired: int
    decisive: int
    unknown: int


@dataclass(frozen=True, slots=True)
class GroupRow:
    group: str
    n: int
    outcomes: Mapping[str, int]
    favorable_rate: float
    ratio: float | None  # favorable rate relative to the best-treated group; None if unavailable
    flagged: bool  # ratio below the threshold in a group large enough to trust


@dataclass(frozen=True, slots=True)
class GroupAudit:
    """Outcome rates per value of a column the policy never reads.

    ``ratio`` is the adverse-impact ratio used in the "four-fifths" screening
    heuristic. It is a prompt to investigate, not a legal or statistical verdict.
    """

    column: str
    favorable_outcome: str
    min_group_size: int
    threshold: float
    reference: str | None
    rows: tuple[GroupRow, ...]

    @property
    def flagged(self) -> tuple[GroupRow, ...]:
        return tuple(r for r in self.rows if r.flagged)


class GroupTally:
    """Accumulates outcomes per group while records stream past."""

    def __init__(self, column: str) -> None:
        self.column = column
        self._counts: dict[str, Counter[str]] = {}

    def add(self, record: Mapping[str, Any], outcome: str) -> None:
        raw = lookup(record, self.column)
        label = str(raw).strip() if raw is not None and str(raw).strip() else MISSING_GROUP
        self._counts.setdefault(label, Counter())[outcome] += 1

    def audit(
        self, policy: Policy, min_group_size: int, threshold: float = FOUR_FIFTHS
    ) -> GroupAudit:
        favorable = policy.outcomes[0]
        ranked = sorted(self._counts.items(), key=lambda kv: (-sum(kv[1].values()), kv[0]))
        kept, rest = ranked[:MAX_GROUPS], ranked[MAX_GROUPS:]
        merged: list[tuple[str, Counter[str]]] = list(kept)
        if rest:
            other: Counter[str] = Counter()
            for _, counter in rest:
                other.update(counter)
            merged.append((OTHER_GROUP, other))

        def rate(c: Counter[str]) -> float:
            return c[favorable] / sum(c.values())

        eligible = [
            (g, rate(c))
            for g, c in merged
            if g != OTHER_GROUP and sum(c.values()) >= min_group_size
        ]
        best = max(eligible, key=lambda kv: kv[1], default=None)
        rows = []
        for group, counter in merged:
            n = sum(counter.values())
            r = rate(counter)
            ratio = (
                r / best[1] if best is not None and best[1] > 0 and group != OTHER_GROUP else None
            )
            flagged = ratio is not None and n >= min_group_size and ratio < threshold
            rows.append(GroupRow(group, n, dict(counter), r, ratio, flagged))
        return GroupAudit(
            self.column,
            favorable,
            min_group_size,
            threshold,
            best[0] if best else None,
            tuple(rows),
        )


@dataclass(frozen=True, slots=True)
class SimulationResult:
    policy: PolicyRef
    total: int
    outcomes: Mapping[str, int]
    rules: tuple[RuleStats, ...]
    defaulted: int
    escalated: int
    with_warnings: int
    missing: Mapping[str, int]  # null count per rule input field, only where > 0
    warning_samples: tuple[str, ...]
    groups: GroupAudit | None = None
    never_fired: tuple[str, ...] = field(default=())


class _RuleCounter:
    def __init__(self, policy: Policy) -> None:
        self.policy = policy
        self.total = 0
        self.outcomes: Counter[str] = Counter()
        self.fired: Counter[str] = Counter()
        self.decisive: Counter[str] = Counter()
        self.unknown: Counter[str] = Counter()
        self.missing: Counter[str] = Counter()
        self.defaulted = self.escalated = self.with_warnings = 0
        self.warning_samples: list[str] = []
        self._inputs = tuple(dict.fromkeys(f for r in policy.rules for f in r.inputs))

    def add(self, decision: Decision) -> None:
        self.total += 1
        self.outcomes[decision.outcome] += 1
        self.fired.update(decision.fired)
        self.decisive.update(decision.decisive if not decision.escalated else ())
        self.unknown.update(decision.unknown)
        self.defaulted += decision.defaulted
        self.escalated += decision.escalated
        for name in self._inputs:
            if decision.facts.get(name) is None:
                self.missing[name] += 1
        if decision.warnings:
            self.with_warnings += 1
            if len(self.warning_samples) < 5:
                self.warning_samples.append(decision.warnings[0])

    def result(self, groups: GroupAudit | None) -> SimulationResult:
        rules = tuple(
            RuleStats(r.id, r.outcome, self.fired[r.id], self.decisive[r.id], self.unknown[r.id])
            for r in self.policy.rules
        )
        return SimulationResult(
            policy=self.policy.ref,
            total=self.total,
            outcomes={o: self.outcomes[o] for o in self.policy.outcomes},
            rules=rules,
            defaulted=self.defaulted,
            escalated=self.escalated,
            with_warnings=self.with_warnings,
            missing={k: v for k, v in self.missing.items() if v},
            warning_samples=tuple(self.warning_samples),
            groups=groups,
            never_fired=tuple(r.rule_id for r in rules if r.fired == 0),
        )


def simulate(
    policy: Policy,
    records: Iterable[Mapping[str, Any]],
    *,
    group_by: str | None = None,
    min_group_size: int = 30,
) -> SimulationResult:
    """Run every record through ``policy`` and summarise what happened.

    Args:
        policy: the policy to evaluate.
        records: raw records (CSV rows or JSON objects).
        group_by: optional column to break outcomes down by. It need not be part of the
            policy schema; auditing on attributes a policy never reads is the point.
        min_group_size: groups smaller than this are shown but never flagged.
    """
    counter = _RuleCounter(policy)
    tally = GroupTally(group_by) if group_by else None
    for record in records:
        decision = policy.decide(record)
        counter.add(decision)
        if tally is not None:
            tally.add(record, decision.outcome)
    return counter.result(tally.audit(policy, min_group_size) if tally else None)
