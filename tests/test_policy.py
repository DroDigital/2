from __future__ import annotations

from pathlib import Path

import pytest

from rulelens import Policy, PolicyError
from rulelens.diagnostics import Diagnostic
from rulelens.policy import build_policy, load_with_diagnostics, render_template
from tests.helpers import LENDING, TRIAGE


def diagnose(text: str) -> list[Diagnostic]:
    import tomllib

    _, diags = build_policy(tomllib.loads(text))
    return diags


def codes(text: str) -> set[str]:
    return {d.code for d in diagnose(text)}


def test_load_valid_policy() -> None:
    policy = Policy.from_toml(LENDING)
    assert policy.name == "lending"
    assert policy.outcomes == ("approve", "refer", "decline")
    assert [r.id for r in policy.rules] == ["LOW_SCORE", "HIGH_DTI", "THIN_FILE"]
    assert policy.rule("HIGH_DTI").inputs == ("debt", "income")  # through the derived field
    assert policy.severity("decline") == 2


def test_fingerprint_is_stable_and_sensitive_to_behaviour() -> None:
    a = Policy.from_toml(LENDING)
    b = Policy.from_toml(LENDING)
    assert a.fingerprint == b.fingerprint
    assert len(a.fingerprint) == 12
    changed = LENDING.replace("credit_score < 580", "credit_score < 590")
    assert Policy.from_toml(changed).fingerprint != a.fingerprint


def test_fingerprint_ignores_whitespace_comments_descriptions_and_tests() -> None:
    base = Policy.from_toml(LENDING)
    noisy = LENDING.replace("credit_score < 580", "credit_score   <   580  ") + "\n# a comment\n"
    noisy = noisy.replace('id = "LOW_SCORE"', 'id = "LOW_SCORE"\ndescription = "docs only"')
    noisy += '\n[[tests]]\nname = "t"\ninput = { credit_score = 500 }\nexpect = "decline"\n'
    assert Policy.from_toml(noisy).fingerprint == base.fingerprint


def test_load_from_file(tmp_path: Path) -> None:
    path = tmp_path / "p.toml"
    path.write_text(TRIAGE)
    assert Policy.load(path).origin == str(path)


def test_missing_file_is_a_policy_error(tmp_path: Path) -> None:
    with pytest.raises(PolicyError, match="cannot read"):
        Policy.load(tmp_path / "nope.toml")


def test_invalid_toml_is_a_policy_error() -> None:
    with pytest.raises(PolicyError, match="invalid TOML"):
        Policy.from_toml("[policy\nname = 1")


def test_invalid_toml_file_yields_diagnostic(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text("not = [valid")
    policy, diags = load_with_diagnostics(path)
    assert policy is None
    assert diags[0].code == "E100"


def test_nested_schema_tables_flatten_to_dotted_names() -> None:
    text = (
        TRIAGE.replace('amount = "number"', 'claim.amount = "number"')
        .replace("amount <", "claim.amount <")
        .replace("amount >=", "claim.amount >=")
    )
    policy = Policy.from_toml(text)
    assert "claim.amount" in policy.schema
    assert (
        policy.decide({"claim": {"amount": 100}, "tier": "gold", "fraud_flag": False}).outcome
        == "auto"
    )


def test_error_messages_aggregate_all_problems() -> None:
    bad = LENDING.replace("credit_score < 580", "credit_scor < 580").replace(
        'outcome = "refer"', 'outcome = "maybe"'
    )
    with pytest.raises(PolicyError) as info:
        Policy.from_toml(bad)
    text = str(info.value)
    assert "E002" in text and "E107" in text


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda t: t.replace("[policy]", "[nope]"), "E102"),
        (lambda t: t.replace('strategy = "most_severe"', 'strategy = "random"'), "E102"),
        (
            lambda t: t.replace('outcomes = ["approve", "refer", "decline"]', 'outcomes = ["a"]'),
            "E102",
        ),
        (lambda t: t.replace('default = "approve"', 'default = "x"'), "E102"),
        (lambda t: t.replace('on_unknown = "refer"', 'on_unknown = "x"'), "E102"),
        (lambda t: t.replace('income = "number"', 'income = "money"'), "E103"),
        (lambda t: t.replace("[schema]", "[schemaa]"), "E103"),
        (lambda t: t.replace('dti = "debt / (income / 12)"', 'income = "1"'), "E104"),
        (lambda t: t.replace('dti = "debt / (income / 12)"', "dti = 5"), "E104"),
        (lambda t: t.replace("debt / (income / 12)", "debt / (income /"), "E001"),
        (lambda t: t.replace("debt / (income / 12)", "debt / wages"), "E002"),
        (lambda t: t.replace("credit_score < 580", "credit_score + 1"), "E108"),
        (lambda t: t.replace("credit_score < 580", "credit_score < 'x'"), "E003"),
        (lambda t: t.replace('id = "HIGH_DTI"', 'id = "LOW_SCORE"'), "E106"),
        (lambda t: t.replace('id = "HIGH_DTI"', 'id = "1bad id"'), "E105"),
        (lambda t: t.replace('outcome = "refer"', 'outcome = "zzz"'), "E107"),
        (lambda t: t + "\nbogus = 1\n", "E101"),
        (lambda t: t.replace('when = "dti > 0.43"', "when = 5"), "E105"),
        (
            lambda t: t.replace('reason = "Mid-range score without employment"', "reason = 5"),
            "E105",
        ),
    ],
)
def test_validation_codes(mutation: object, code: str) -> None:
    text = mutation(LENDING)  # type: ignore[operator]
    assert code in codes(text)


def test_rules_are_required() -> None:
    head = LENDING.split("[[rules]]")[0]
    assert "E105" in codes(head)


def test_missing_reason_warns_and_falls_back() -> None:
    text = LENDING.replace(
        'reason = "Mid-range score without employment"\n', 'description = "Mid-range"\n'
    )
    diags = diagnose(text)
    assert any(d.code == "W108" and d.rule_id == "THIN_FILE" for d in diags)
    policy = Policy.from_toml(text)
    assert policy.rule("THIN_FILE").reason == "Mid-range"


def test_reason_template_problems_are_warnings() -> None:
    text = LENDING.replace(
        "{credit_score} is below 580", "{credit_scor} is {credit_score:@@@@@@@@@@@@@@@@@@}"
    )
    diags = [d for d in diagnose(text) if d.code == "W105"]
    assert len(diags) == 2


def test_diagnostics_carry_caret_snippet() -> None:
    diags = diagnose(LENDING.replace("credit_score < 580", "credit_scor < 580"))
    err = next(d for d in diags if d.code == "E002")
    assert err.snippet is not None and "^" in err.snippet
    assert err.location == "rules[LOW_SCORE].when"
    assert "did you mean 'credit_score'" in err.message


def test_embedded_tests_are_validated() -> None:
    def check(inp: str = "credit_score = 500", exp: str = "decline", extra: str = "") -> set[str]:
        block = f'\n[[tests]]\nname = "x"\ninput = {{ {inp} }}\nexpect = "{exp}"\n{extra}'
        return codes(LENDING + block)

    assert check() == set()
    assert "E110" in check(inp="nope = 1")
    assert "E109" in check(exp="maybe")
    assert "E109" in check(extra='expect_rules = ["GHOST"]')
    assert "E109" in check(extra='expect_rules = "LOW_SCORE"')
    assert "E101" in check(extra="bogus = 1")
    assert "E109" in codes("tests = 5\n" + LENDING)


def test_a_test_needs_input_and_expect() -> None:
    assert "E109" in codes(LENDING + "\n[[tests]]\n")
    assert "E109" in codes(LENDING + '\n[[tests]]\nname = "only name"\n')


def test_render_template() -> None:
    facts = {"a": 0.456, "b": None, "c": "x", "d": 3}
    assert render_template("{a:.1%} {b} {c} {d:>3}", facts) == "45.6% n/a x   3"
    assert render_template("literal {{braces}} {unknown}", facts) == "literal {braces} n/a"
    assert render_template("{c:.2f}", facts) == "x"  # bad spec for the type falls back
    assert render_template("{a:@@@}", facts) == "0.456"  # disallowed spec is ignored
