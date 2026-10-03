from __future__ import annotations

from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rulelens import DataError, Policy
from rulelens.schema import coerce, lookup
from tests.helpers import LENDING, TRIAGE, lending, triage

GOOD = {"credit_score": 720, "income": 90000, "debt": 1000, "employed": True}


# -- strategies -------------------------------------------------------------------------------


def test_most_severe_picks_worst_outcome_and_lists_all_its_reasons() -> None:
    d = lending().decide({**GOOD, "credit_score": 500, "debt": 6000, "income": 60000})
    assert d.outcome == "decline"
    assert set(d.decisive) == {"LOW_SCORE", "HIGH_DTI"}
    assert len(d.reasons) == 2
    assert "Credit score 500 is below 580" in d.reasons
    assert "Debt-to-income 120% exceeds 43%" in d.reasons


def test_most_severe_lower_outcomes_are_not_decisive() -> None:
    d = lending().decide({**GOOD, "credit_score": 500, "employed": False})
    assert d.fired == ("LOW_SCORE", "THIN_FILE")
    assert d.decisive == ("LOW_SCORE",)


def test_first_match_uses_rule_order() -> None:
    d = triage().decide({"amount": 20000, "fraud_flag": True, "tier": "gold"})
    assert d.outcome == "deny"
    assert d.decisive == ("FRAUD",)
    assert d.fired == ("FRAUD", "BIG")


def test_default_applies_when_nothing_fires() -> None:
    d = triage().decide({"amount": 800, "fraud_flag": False, "tier": "bronze"})
    assert d.outcome == "review"
    assert d.defaulted is True
    assert d.decisive == ()
    assert d.reasons == ("No rule matched; the default outcome applies.",)


def test_decision_records_policy_identity() -> None:
    d = lending().decide(GOOD)
    assert d.policy.name == "lending"
    assert d.policy.fingerprint == lending().fingerprint
    assert str(d.policy).startswith("lending v1.0 [")


# -- missing data -----------------------------------------------------------------------------


def test_missing_field_makes_dependent_rules_unknown_not_false() -> None:
    d = lending().decide({"income": 90000, "debt": 1000, "employed": True})
    by_id = {r.rule_id: r for r in d.rule_results}
    assert by_id["LOW_SCORE"].result is None
    assert by_id["LOW_SCORE"].missing == ("credit_score",)
    assert by_id["HIGH_DTI"].result is False
    assert d.missing_fields == ("credit_score",)


def test_on_unknown_escalates_when_missing_data_could_raise_severity() -> None:
    d = lending().decide({"income": 90000, "debt": 1000, "employed": True})
    assert d.outcome == "refer"
    assert d.escalated is True
    assert d.defaulted is False
    assert "credit_score" in d.reasons[0]
    assert "LOW_SCORE" in d.decisive


def test_on_unknown_never_downgrades_a_decision() -> None:
    d = lending().decide({"income": 1000, "debt": 5000, "employed": True})
    assert d.outcome == "decline"  # HIGH_DTI fired; the unknown LOW_SCORE can't make it worse
    assert d.escalated is False


def test_on_unknown_ignored_when_unknown_rule_cannot_change_outcome() -> None:
    text = LENDING.replace('on_unknown = "refer"', 'on_unknown = "approve"')
    d = Policy.from_toml(text).decide({"income": 90000, "debt": 1000, "employed": True})
    assert d.outcome == "approve" and d.escalated is False


def test_without_on_unknown_missing_data_is_just_not_fired() -> None:
    text = LENDING.replace('on_unknown = "refer"\n', "")
    d = Policy.from_toml(text).decide({"income": 90000, "debt": 1000, "employed": True})
    assert d.outcome == "approve"
    assert d.missing_fields == ("credit_score",)


def test_first_match_escalates_only_for_unknown_rules_ahead_of_the_decisive_one() -> None:
    text = TRIAGE.replace('default = "review"', 'default = "review"\non_unknown = "review"')
    policy = Policy.from_toml(text)
    # fraud_flag unknown, FRAUD is ahead of the matching SMALL rule -> escalate.
    d = policy.decide({"amount": 100, "tier": "gold"})
    assert d.outcome == "review" and d.escalated
    # Unknown rule *behind* the decisive rule is irrelevant.
    d = policy.decide({"amount": 100, "tier": "gold", "fraud_flag": False})
    assert d.outcome == "auto" and not d.escalated


def test_first_match_unknown_rule_with_same_outcome_does_not_escalate() -> None:
    text = TRIAGE.replace('default = "review"', 'default = "review"\non_unknown = "review"')
    d = Policy.from_toml(text).decide({"fraud_flag": True, "tier": "gold"})
    assert d.outcome == "deny" and not d.escalated


# -- coercion ---------------------------------------------------------------------------------


def test_csv_style_strings_are_coerced() -> None:
    d = lending().decide(
        {"credit_score": "720", "income": "90,000" and "90000", "debt": " 1000 ", "employed": "yes"}
    )
    assert d.facts["credit_score"] == 720
    assert d.facts["employed"] is True
    assert d.outcome == "approve"


def test_blank_values_are_missing() -> None:
    d = lending().decide({"credit_score": "", "income": "90000", "debt": "1", "employed": "true"})
    assert d.facts["credit_score"] is None


def test_bad_values_become_missing_with_a_warning() -> None:
    d = lending().decide({**GOOD, "credit_score": "seven hundred"})
    assert d.facts["credit_score"] is None
    assert any("credit_score" in w and "treated as missing" in w for w in d.warnings)


def test_strict_mode_raises() -> None:
    with pytest.raises(DataError, match="credit_score"):
        lending().decide({**GOOD, "credit_score": "abc"}, strict=True)


def test_runtime_warnings_are_labelled_with_their_source() -> None:
    d = lending().decide({**GOOD, "income": 0})
    assert any(w.startswith("derived 'dti':") and "division by zero" in w for w in d.warnings)
    assert d.facts["dti"] is None


@pytest.mark.parametrize(
    ("value", "declared", "expected"),
    [
        ("42", "int", 42),
        (42.0, "int", 42),
        ("3.5", "number", 3.5),
        (7, "number", 7),
        ("TRUE", "bool", True),
        ("no", "bool", False),
        (1, "string", "1"),
        ("2025-03-04", "date", date(2025, 3, 4)),
        ("2025-03-04T10:00:00", "date", date(2025, 3, 4)),
        ("", "int", None),
        (None, "date", None),
    ],
)
def test_coerce_success(value: object, declared: str, expected: object) -> None:
    assert coerce(value, declared) == (expected, None)


@pytest.mark.parametrize(
    ("value", "declared"),
    [
        ("4.5", "int"),
        ("abc", "number"),
        (True, "number"),
        (float("nan"), "number"),
        ("inf", "number"),
        ("maybe", "bool"),
        ("2025-13-40", "date"),
        (["x"], "string"),
        (12, "date"),
    ],
)
def test_coerce_failure(value: object, declared: str) -> None:
    result, problem = coerce(value, declared)
    assert result is None and problem is not None


def test_lookup_flat_nested_and_missing() -> None:
    assert lookup({"a.b": 1}, "a.b") == 1
    assert lookup({"a": {"b": 2}}, "a.b") == 2
    assert lookup({"a": {"b": 2}}, "a.c") is None
    assert lookup({"a": 1}, "a.b") is None
    assert lookup({}, "zzz") is None
    assert lookup(None, "x") is None


# -- tracing ----------------------------------------------------------------------------------


def test_trace_off_by_default_and_on_when_requested() -> None:
    assert all(r.clause is None for r in lending().decide(GOOD).rule_results)
    assert all(r.clause is not None for r in lending().decide(GOOD, trace=True).rule_results)


def test_explanation_shows_values_that_decided_each_clause() -> None:
    d = lending().decide({**GOOD, "credit_score": 600, "employed": False}, trace=True)
    text = d.explain()
    assert "credit_score < 660 → true  [credit_score=600]" in text
    assert "not employed → true  [employed=false]" in text
    assert "credit_score < 580 → false  [credit_score=600]" in text  # why-not for a miss


def test_decision_serialises_to_json() -> None:
    import json

    d = lending().decide({**GOOD, "opened": "2025-01-02"}, trace=True)
    payload = json.loads(json.dumps(d.to_dict()))
    assert payload["outcome"] == "approve"
    assert payload["facts"]["opened"] == "2025-01-02"
    assert payload["policy"]["fingerprint"] == lending().fingerprint
    assert payload["rules"][0]["trace"]["text"] == "credit_score < 580"


def test_decide_many_streams_decisions() -> None:
    outcomes = [d.outcome for d in lending().decide_many([GOOD, {**GOOD, "credit_score": 400}])]
    assert outcomes == ["approve", "decline"]


@given(
    st.fixed_dictionaries(
        {
            "credit_score": st.one_of(st.none(), st.integers(300, 850)),
            "income": st.one_of(st.none(), st.integers(0, 200000)),
            "debt": st.one_of(st.none(), st.integers(0, 50000)),
            "employed": st.one_of(st.none(), st.booleans()),
        }
    )
)
def test_traced_and_untraced_decisions_always_agree(record: dict[str, object]) -> None:
    policy = lending()
    fast = policy.decide(record)
    traced = policy.decide(record, trace=True)
    assert [r.result for r in fast.rule_results] == [r.result for r in traced.rule_results]
    assert (fast.outcome, fast.decisive, fast.reasons) == (
        traced.outcome,
        traced.decisive,
        traced.reasons,
    )
