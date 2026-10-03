from __future__ import annotations

from typing import Any

from rulelens.simulate import GroupTally, simulate
from tests.helpers import lending

GOOD: dict[str, Any] = {"credit_score": 720, "income": 90000, "debt": 1000, "employed": True}


def records(n_good: int = 0, n_bad: int = 0, region: str = "N") -> list[dict[str, Any]]:
    return [{**GOOD, "region": region} for _ in range(n_good)] + [
        {**GOOD, "credit_score": 400, "region": region} for _ in range(n_bad)
    ]


def test_counts_and_rule_stats() -> None:
    data = [*records(3, 2), {"income": 90000, "debt": 1000, "employed": True}]  # last: no score
    result = simulate(lending(), data)
    assert result.total == 6
    assert result.outcomes == {"approve": 3, "refer": 1, "decline": 2}
    assert result.escalated == 1
    stats = {r.rule_id: r for r in result.rules}
    assert (stats["LOW_SCORE"].fired, stats["LOW_SCORE"].decisive, stats["LOW_SCORE"].unknown) == (
        2,
        2,
        1,
    )
    assert stats["HIGH_DTI"].fired == 0
    assert result.never_fired == ("HIGH_DTI", "THIN_FILE")
    assert result.missing == {"credit_score": 1}
    assert result.policy.name == "lending"


def test_default_and_warning_accounting() -> None:
    result = simulate(lending(), [GOOD, {**GOOD, "credit_score": "oops"}])
    assert result.defaulted == 1
    assert result.with_warnings == 1
    assert "credit_score" in result.warning_samples[0]


def test_empty_input() -> None:
    result = simulate(lending(), [])
    assert result.total == 0 and result.outcomes == {"approve": 0, "refer": 0, "decline": 0}


# -- group audit -------------------------------------------------------------------------------


def grouped(spec: dict[str, tuple[int, int]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for region, (good, bad) in spec.items():
        out += records(good, bad, region)
    return out


def test_group_audit_ratio_and_flag() -> None:
    data = grouped({"A": (80, 20), "B": (50, 50), "C": (9, 1)})
    audit = simulate(lending(), data, group_by="region").groups
    assert audit is not None
    rows = {r.group: r for r in audit.rows}
    assert audit.reference == "A" and audit.favorable_outcome == "approve"
    assert rows["A"].ratio == 1.0 and not rows["A"].flagged
    assert rows["B"].ratio == 0.625 and rows["B"].flagged
    # C is below the minimum size: reported, but neither a reference nor flagged.
    assert rows["C"].n == 10 and not rows["C"].flagged
    assert audit.flagged == (rows["B"],)


def test_group_audit_respects_min_group_size_and_threshold() -> None:
    data = grouped({"A": (80, 20), "B": (50, 50)})
    audit = simulate(lending(), data, group_by="region", min_group_size=200).groups
    assert audit is not None and audit.reference is None and not audit.flagged
    assert all(r.ratio is None for r in audit.rows)


def test_group_column_need_not_be_in_the_schema_and_missing_values_are_labelled() -> None:
    data = [{**GOOD}, {**GOOD, "region": ""}, {**GOOD, "region": "N"}]
    audit = simulate(lending(), data, group_by="region").groups
    assert audit is not None
    assert {r.group for r in audit.rows} == {"(missing)", "N"}


def test_many_groups_collapse_into_other() -> None:
    data = [{**GOOD, "region": f"g{i:02d}"} for i in range(30)]
    audit = simulate(lending(), data, group_by="region", min_group_size=1).groups
    assert audit is not None
    assert len(audit.rows) == 21
    other = audit.rows[-1]
    assert other.group == "(other)" and other.n == 10 and other.ratio is None and not other.flagged


def test_zero_favorable_best_group_gives_no_ratios() -> None:
    data = records(0, 40, "A")
    audit = simulate(lending(), data, group_by="region").groups
    assert audit is not None and audit.rows[0].ratio is None


def test_tally_supports_dotted_columns() -> None:
    tally = GroupTally("applicant.region")
    tally.add({"applicant": {"region": "X"}}, "approve")
    audit = tally.audit(lending(), 1)
    assert audit.rows[0].group == "X"
