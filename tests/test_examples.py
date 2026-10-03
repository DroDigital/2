"""The shipped examples are documentation, so they are tested like code."""

from __future__ import annotations

import importlib.util
import random
from pathlib import Path
from typing import Any

import pytest

from rulelens import Policy
from rulelens.analysis import lint
from rulelens.dataio import read_records
from rulelens.diff import diff_policies
from rulelens.policy import load_with_diagnostics
from rulelens.simulate import simulate
from rulelens.testing import run_tests

ROOT = Path(__file__).resolve().parent.parent / "examples"
POLICIES = sorted(ROOT.glob("*/policy*.toml"))
DATASETS = {
    "lending": "applications.csv",
    "fraud": "transactions.csv",
    "claims": "claims.csv",
    "trials": "screening.csv",
}


def ids(paths: list[Path]) -> list[str]:
    return [f"{p.parent.name}/{p.name}" for p in paths]


@pytest.mark.parametrize("path", POLICIES, ids=ids(POLICIES))
def test_policy_loads_and_has_no_errors(path: Path) -> None:
    policy, diagnostics = load_with_diagnostics(path)
    assert policy is not None
    assert [d for d in diagnostics if d.severity == "error"] == []


@pytest.mark.parametrize(
    "path",
    [p for p in POLICIES if p.parent.name != "lint_demo"],
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_example_policies_lint_clean(path: Path) -> None:
    policy = Policy.load(path)
    assert [d.code for d in lint(policy)] == []


@pytest.mark.parametrize(
    "path",
    [p for p in POLICIES if p.parent.name != "lint_demo"],
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_embedded_tests_pass(path: Path) -> None:
    results = run_tests(Policy.load(path))
    assert results, "every example policy should ship regression tests"
    failures = [(r.name, r.failures) for r in results if not r.passed]
    assert failures == []


def test_lint_demo_flags_exactly_the_planted_mistakes() -> None:
    findings = {(d.code, d.rule_id) for d in lint(Policy.load(ROOT / "lint_demo" / "policy.toml"))}
    assert ("W102", "FREE_TIER_CRITICAL") in findings
    assert ("W101", "IMPOSSIBLE") in findings
    assert {c for c, _ in findings if c.startswith("W")} == {"W101", "W102"}


@pytest.mark.parametrize(("folder", "dataset"), DATASETS.items())
def test_every_dataset_runs_cleanly(folder: str, dataset: str) -> None:
    policy = Policy.load(ROOT / folder / "policy.toml")
    result = simulate(policy, read_records(ROOT / folder / dataset))
    assert result.total >= 500
    assert sum(result.outcomes.values()) == result.total
    # A useful example exercises every outcome and nearly every rule.
    assert all(n > 0 for n in result.outcomes.values()), result.outcomes
    assert len(result.never_fired) <= 1, result.never_fired


def test_lending_story_holds() -> None:
    """The README quotes these properties of the lending diff."""
    old, new = (Policy.load(ROOT / "lending" / f) for f in ("policy.toml", "policy_v2.toml"))
    records = list(read_records(ROOT / "lending" / "applications.csv"))
    result = diff_policies(old, new, records, group_by="region")
    assert result.changed_rate > 0.1  # far more individual churn ...
    net_shift = abs(result.new_counts["approve"] - result.old_counts["approve"])
    assert net_shift < result.changed / 10  # ... than the headline approval rate reveals
    assert result.old_groups is not None and result.new_groups is not None
    assert [r.group for r in result.old_groups.flagged] == ["South"]
    assert [r.group for r in result.new_groups.flagged] == ["South"]


def test_fraud_diff_reduces_review_queue() -> None:
    old, new = (Policy.load(ROOT / "fraud" / f) for f in ("policy.toml", "policy_v2.toml"))
    result = diff_policies(old, new, read_records(ROOT / "fraud" / "transactions.csv"))
    assert result.new_counts["review"] < result.old_counts["review"]
    assert result.new_counts["block"] > result.old_counts["block"]


def test_committed_datasets_match_the_generator(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("generate_data", ROOT / "generate_data.py")
    assert spec is not None and spec.loader is not None
    module: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.HERE = tmp_path
    (tmp_path.parent / "examples").mkdir(exist_ok=True)
    for folder in DATASETS:
        (tmp_path / folder).mkdir()
    rng = random.Random(20240601)
    module.write(tmp_path / "lending" / "applications.csv", module.lending(rng))
    module.write(tmp_path / "fraud" / "transactions.csv", module.fraud(rng))
    module.write(tmp_path / "claims" / "claims.csv", module.claims(rng))
    module.write(tmp_path / "trials" / "screening.csv", module.trials(rng))
    for folder, name in DATASETS.items():
        assert (tmp_path / folder / name).read_bytes() == (ROOT / folder / name).read_bytes(), name
