from __future__ import annotations

from typing import Any

from rulelens import Policy
from rulelens.diff import diff_policies
from tests.helpers import LENDING, lending

BASE: dict[str, Any] = {"credit_score": 720, "income": 90000, "debt": 1000, "employed": True}


def variant(**changes: str) -> Policy:
    """The lending policy with text substituted, e.g. ``variant(**{"580": "600"})``."""
    text = LENDING
    for old, new in changes.items():
        text = text.replace(old, new)
    return Policy.from_toml(text)


def test_identical_policies_change_nothing() -> None:
    data = [BASE, {**BASE, "credit_score": 400}]
    result = diff_policies(lending(), lending(), data)
    assert result.total == 2 and result.changed == 0 and result.rationale_changed == 0
    assert result.changed_rate == 0.0 and result.drivers == () and result.examples == ()
    assert result.old.fingerprint == result.new.fingerprint
    assert result.transitions == {("approve", "approve"): 1, ("decline", "decline"): 1}


def test_threshold_change_moves_exactly_the_records_between_the_thresholds() -> None:
    new = variant(**{"580": "600"})
    data = [{**BASE, "credit_score": s} for s in (570, 590, 599, 600, 700)]
    result = diff_policies(lending(), new, data)
    assert result.changed == 2  # 590 and 599 become declines
    assert result.transitions[("approve", "decline")] == 2
    assert result.old_counts == {"approve": 4, "refer": 0, "decline": 1}
    assert result.new_counts == {"approve": 2, "refer": 0, "decline": 3}
    assert [(d.old, d.new, d.count) for d in result.drivers] == [("(default)", "LOW_SCORE", 2)]


def test_rationale_changes_are_counted_separately() -> None:
    renamed = Policy.from_toml(LENDING.replace("LOW_SCORE", "SCORE_FLOOR"))
    result = diff_policies(lending(), renamed, [{**BASE, "credit_score": 500}])
    assert result.changed == 0 and result.rationale_changed == 1


def test_examples_are_capped_per_transition_and_carry_ids() -> None:
    new = variant(**{"580": "600"})
    data = [{**BASE, "credit_score": 590, "id": f"R{i}"} for i in range(10)]
    result = diff_policies(lending(), new, data, id_field="id", max_examples=3)
    assert len(result.examples) == 3
    first = result.examples[0]
    assert (first.index, first.record_id) == (1, "R0")
    assert first.old_outcome == "approve" and first.new_outcome == "decline"
    assert first.new_reasons == ("Credit score 590 is below 600",)


def test_each_policy_normalises_against_its_own_schema() -> None:
    with_extra = Policy.from_toml(
        LENDING.replace('region = "string"', 'region = "string"\nflag = "bool"')
        + '\n[[rules]]\nid = "FLAG"\nwhen = "flag"\noutcome = "decline"\nreason = "flagged"\n'
    )
    data = [{**BASE, "flag": "true"}, BASE]
    result = diff_policies(lending(), with_extra, data)
    # The flagged record is declined. The other lacks `flag`, so the new FLAG rule is unknown and
    # the fail-safe escalates it: a data gap in the new policy's inputs changes real outcomes.
    assert result.transitions == {("approve", "decline"): 1, ("approve", "refer"): 1}
    assert result.new_missing == {"flag": 1}
    assert result.old_missing == {}


def test_group_audits_for_both_versions() -> None:
    new = variant(**{"580": "750"})
    data = [{**BASE, "credit_score": 720, "region": "A"}] * 40 + [
        {**BASE, "credit_score": 780, "region": "B"}
    ] * 40
    result = diff_policies(lending(), new, data, group_by="region")
    assert result.old_groups is not None and result.new_groups is not None
    assert result.old_groups.flagged == ()  # both groups approved before
    flagged = result.new_groups.flagged  # the stricter floor now rejects all of group A
    assert [(r.group, r.favorable_rate, r.ratio) for r in flagged] == [("A", 0.0, 0.0)]


def test_empty_data() -> None:
    result = diff_policies(lending(), lending(), [])
    assert result.total == 0 and result.changed_rate == 0.0
