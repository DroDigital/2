"""Policy impact analysis: what would change if this policy replaced that one?"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .decision import Decision, PolicyRef
from .policy import Policy
from .schema import lookup
from .simulate import GroupAudit, GroupTally, _RuleCounter

DEFAULT_KEY = "(default)"


@dataclass(frozen=True, slots=True)
class ChangedRecord:
    index: int  # 1-based position in the data file
    record_id: str | None
    old_outcome: str
    new_outcome: str
    old_decisive: tuple[str, ...]
    new_decisive: tuple[str, ...]
    old_reasons: tuple[str, ...]
    new_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Driver:
    """Which rule decided before, which decides now, and how many records moved because of it."""

    old: str
    new: str
    count: int


@dataclass(frozen=True, slots=True)
class DiffResult:
    old: PolicyRef
    new: PolicyRef
    old_outcomes: tuple[str, ...]  # outcome vocabulary of each policy, least to most severe
    new_outcomes: tuple[str, ...]
    total: int
    changed: int  # records whose outcome differs
    rationale_changed: int  # same outcome, but decided by different rules
    transitions: Mapping[tuple[str, str], int]
    old_counts: Mapping[str, int]
    new_counts: Mapping[str, int]
    drivers: tuple[Driver, ...]
    examples: tuple[ChangedRecord, ...]
    old_missing: Mapping[str, int]
    new_missing: Mapping[str, int]
    old_groups: GroupAudit | None = None
    new_groups: GroupAudit | None = None

    @property
    def changed_rate(self) -> float:
        return self.changed / self.total if self.total else 0.0


def _key(decision: Decision) -> str:
    return "+".join(decision.decisive) if decision.decisive else DEFAULT_KEY


def diff_policies(
    old: Policy,
    new: Policy,
    records: Iterable[Mapping[str, Any]],
    *,
    group_by: str | None = None,
    id_field: str | None = None,
    max_examples: int = 5,
    min_group_size: int = 30,
) -> DiffResult:
    """Replay ``records`` through both policies and describe every difference.

    Each policy normalises the raw records against its own schema, so the two
    versions may declare different fields. Fields one version needs but the data
    lacks show up in ``old_missing`` / ``new_missing``.

    Args:
        old: the policy currently in force.
        new: the proposed replacement.
        records: raw historical records.
        group_by: optional column for an outcome-rate audit of both versions.
        id_field: column used to identify records in examples (default: row number).
        max_examples: changed records to keep per (old outcome, new outcome) transition.
        min_group_size: smallest group the audit will flag.
    """
    old_counter, new_counter = _RuleCounter(old), _RuleCounter(new)
    old_tally = GroupTally(group_by) if group_by else None
    new_tally = GroupTally(group_by) if group_by else None
    transitions: Counter[tuple[str, str]] = Counter()
    drivers: Counter[tuple[str, str]] = Counter()
    per_transition: Counter[tuple[str, str]] = Counter()
    examples: list[ChangedRecord] = []
    changed = rationale_changed = total = 0

    for index, record in enumerate(records, start=1):
        total += 1
        before, after = old.decide(record), new.decide(record)
        old_counter.add(before)
        new_counter.add(after)
        if old_tally is not None and new_tally is not None:
            old_tally.add(record, before.outcome)
            new_tally.add(record, after.outcome)
        transitions[(before.outcome, after.outcome)] += 1

        if before.outcome != after.outcome:
            changed += 1
            drivers[(_key(before), _key(after))] += 1
            pair = (before.outcome, after.outcome)
            if per_transition[pair] < max_examples:
                per_transition[pair] += 1
                ident = lookup(record, id_field) if id_field else None
                examples.append(
                    ChangedRecord(
                        index,
                        None if ident is None else str(ident),
                        before.outcome,
                        after.outcome,
                        before.decisive,
                        after.decisive,
                        before.reasons,
                        after.reasons,
                    )
                )
        elif set(before.decisive) != set(after.decisive):
            rationale_changed += 1

    old_result, new_result = old_counter.result(None), new_counter.result(None)
    return DiffResult(
        old=old.ref,
        new=new.ref,
        old_outcomes=old.outcomes,
        new_outcomes=new.outcomes,
        total=total,
        changed=changed,
        rationale_changed=rationale_changed,
        transitions=dict(transitions),
        old_counts=old_result.outcomes,
        new_counts=new_result.outcomes,
        drivers=tuple(Driver(o, n, c) for (o, n), c in drivers.most_common()),
        examples=tuple(examples),
        old_missing=old_result.missing,
        new_missing=new_result.missing,
        old_groups=old_tally.audit(old, min_group_size) if old_tally else None,
        new_groups=new_tally.audit(new, min_group_size) if new_tally else None,
    )
