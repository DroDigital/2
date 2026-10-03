"""Shared test fixtures: small policies built from TOML text."""

from __future__ import annotations

from rulelens import Policy

LENDING = """
[policy]
name = "lending"
version = "1.0"
strategy = "most_severe"
outcomes = ["approve", "refer", "decline"]
default = "approve"
on_unknown = "refer"

[schema]
credit_score = "int"
income = "number"
debt = "number"
employed = "bool"
region = "string"
opened = "date"

[derived]
dti = "debt / (income / 12)"

[[rules]]
id = "LOW_SCORE"
when = "credit_score < 580"
outcome = "decline"
reason = "Credit score {credit_score} is below 580"

[[rules]]
id = "HIGH_DTI"
when = "dti > 0.43"
outcome = "decline"
reason = "Debt-to-income {dti:.0%} exceeds 43%"

[[rules]]
id = "THIN_FILE"
when = "credit_score < 660 and not employed"
outcome = "refer"
reason = "Mid-range score without employment"
"""

TRIAGE = """
[policy]
name = "triage"
version = "2"
strategy = "first_match"
outcomes = ["auto", "review", "deny"]
default = "review"

[schema]
amount = "number"
fraud_flag = "bool"
tier = "string"

[[rules]]
id = "FRAUD"
when = "fraud_flag"
outcome = "deny"
reason = "Fraud flag set"

[[rules]]
id = "SMALL"
when = "amount < 500 and tier in ['gold', 'silver']"
outcome = "auto"
reason = "Small claim from a trusted tier"

[[rules]]
id = "BIG"
when = "amount >= 10000"
outcome = "review"
reason = "Large claim"
"""


def lending() -> Policy:
    return Policy.from_toml(LENDING)


def triage() -> Policy:
    return Policy.from_toml(TRIAGE)
