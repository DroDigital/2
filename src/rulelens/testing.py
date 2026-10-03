"""Run the regression cases embedded in a policy's ``[[tests]]`` tables."""

from __future__ import annotations

from dataclasses import dataclass

from .decision import Decision
from .policy import Policy


@dataclass(frozen=True, slots=True)
class CaseResult:
    name: str
    passed: bool
    failures: tuple[str, ...]
    decision: Decision


def run_tests(policy: Policy) -> list[CaseResult]:
    """Evaluate every embedded test and report which ones disagree with the policy."""
    results: list[CaseResult] = []
    for case in policy.tests:
        decision = policy.decide(case.input, trace=True)
        failures: list[str] = []
        if decision.outcome != case.expect:
            failures.append(f"expected outcome '{case.expect}', got '{decision.outcome}'")
        if case.expect_rules is not None and set(decision.decisive) != set(case.expect_rules):
            want = ", ".join(sorted(case.expect_rules)) or "(none)"
            got = ", ".join(sorted(decision.decisive)) or "(none)"
            failures.append(f"expected decisive rules [{want}], got [{got}]")
        results.append(CaseResult(case.name, not failures, tuple(failures), decision))
    return results
