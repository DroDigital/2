from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from rulelens import Policy
from rulelens.diagnostics import Diagnostic
from rulelens.diff import DiffResult, diff_policies
from rulelens.html_report import render_diff_html, render_simulation_html
from rulelens.report import (
    Style,
    bar,
    diff_to_dict,
    pct,
    plural,
    render_diff,
    render_lint,
    render_simulation,
    render_tests,
    simulation_to_dict,
)
from rulelens.simulate import simulate
from rulelens.testing import run_tests
from tests.helpers import LENDING, lending

PLAIN = Style(False)
GOOD: dict[str, Any] = {"credit_score": 720, "income": 90000, "debt": 1000, "employed": True}
STAMP = datetime(2025, 1, 2, 3, 4, tzinfo=UTC)


def data(region: str = "N") -> list[dict[str, Any]]:
    return (
        [{**GOOD, "region": region}] * 40
        + [{**GOOD, "credit_score": 400, "region": region}] * 5
        + [{"income": 90000, "debt": 1000, "employed": True, "region": region}] * 5
    )


def diff_result() -> DiffResult:
    new = Policy.from_toml(LENDING.replace("580", "730"))
    rows = data("A") + [{**r, "region": "B"} for r in data("B")]
    return diff_policies(lending(), new, rows, group_by="region", id_field="region")


# -- helpers --------------------------------------------------------------------------------


def test_small_helpers() -> None:
    assert bar(0.5, 10) == "█████░░░░░"
    assert bar(2, 4) == "████" and bar(-1, 4) == "░░░░"
    assert pct(1, 4) == "25.0%" and pct(1, 0) == "–"
    assert plural(1, "error") == "1 error" and plural(2, "error") == "2 errors"


def test_style_adds_ansi_only_when_enabled() -> None:
    assert Style(True).red("x") == "\x1b[31mx\x1b[0m"
    assert Style(False).red("x") == "x"
    s = Style(True)
    assert (
        "32" in s.outcome("a", 0, 3)
        and "33" in s.outcome("b", 1, 3)
        and "31" in s.outcome("c", 2, 3)
    )


# -- text renderers -------------------------------------------------------------------------


def test_render_lint() -> None:
    assert "no problems found" in render_lint("p.toml", [], PLAIN)
    diags = [
        Diagnostic(
            "error",
            "E002",
            "unknown field 'x'",
            "R",
            "rules[R].when",
            "unknown field\n    x\n    ^",
        ),
        Diagnostic("warning", "W101", "dead", "R2", None),
        Diagnostic("info", "I201", "unused"),
    ]
    text = render_lint("p.toml", diags, PLAIN)
    assert text.startswith("p.toml: 1 error, 1 warning, 1 info")
    assert "error E002 rules[R].when: unknown field 'x'" in text
    assert "    ^" in text  # caret snippet preserved


def test_render_tests() -> None:
    policy = Policy.from_toml(
        LENDING
        + '\n[[tests]]\nname = "ok"\ninput = { credit_score = 700, income = 90000, debt = 1000, employed = true }\nexpect = "approve"\n'
        '[[tests]]\nname = "bad"\ninput = { credit_score = 500, income = 90000, debt = 1000, employed = true }\nexpect = "approve"\n'
    )
    text = render_tests("p", run_tests(policy), PLAIN)
    assert "✔ ok" in text and "✘ bad" in text
    assert "expected outcome 'approve', got 'decline'" in text
    assert "1/2 passed" in text
    assert "no [[tests]]" in render_tests("p", [], PLAIN)


def test_expect_rules_mismatch_is_reported() -> None:
    policy = Policy.from_toml(
        LENDING
        + '\n[[tests]]\nname = "w"\ninput = { credit_score = 500, income = 90000, debt = 1000, employed = true }\nexpect = "decline"\nexpect_rules = []\n'
    )
    (case,) = run_tests(policy)
    assert (
        not case.passed and "expected decisive rules [(none)], got [LOW_SCORE]" in case.failures[0]
    )


def test_render_simulation() -> None:
    text = render_simulation(simulate(lending(), data(), group_by="region"), PLAIN)
    for fragment in (
        "Policy  lending v1.0",
        "Records 50",
        "approve",
        "fell through to the default",
        "escalated because of missing data",
        "Never fired on this data: HIGH_DTI",
        "credit_score is missing in 5 records",
        "Outcome by region",
        "screening heuristic",
    ):
        assert fragment in text, fragment


def test_render_diff() -> None:
    text = render_diff(diff_result(), PLAIN)
    for fragment in (
        "old  lending v1.0",
        "approve → decline",
        "What drove the changes",
        "(default) → LOW_SCORE",
        "Examples",
        "Outcome by region — old policy",
        "Outcome by region — new policy",
    ):
        assert fragment in text, fragment
    assert text.count("screening heuristic") == 1  # footnote printed once


def test_diff_warns_about_inputs_missing_from_the_data() -> None:
    extra = Policy.from_toml(
        LENDING
        + '\n[[rules]]\nid = "X"\nwhen = "region == \'Z\'"\noutcome = "decline"\nreason = "z"\n'
    )
    result = diff_policies(lending(), extra, [GOOD] * 20)
    assert "new policy reads 'region', unknown in 20 records (100.0%)" in render_diff(result, PLAIN)


# -- machine-readable ------------------------------------------------------------------------


def test_dict_forms_are_json_serialisable() -> None:
    sim = json.loads(json.dumps(simulation_to_dict(simulate(lending(), data(), group_by="region"))))
    assert sim["total"] == 50 and sim["groups"]["column"] == "region"
    diff = json.loads(json.dumps(diff_to_dict(diff_result())))
    assert diff["changed"] > 0
    assert {"old", "new", "count"} <= set(diff["transitions"][0])
    assert "old_groups" in diff


# -- HTML ------------------------------------------------------------------------------------


def test_simulation_html_structure() -> None:
    html = render_simulation_html(
        simulate(lending(), data(), group_by="region"), dataset="apps.csv", generated_at=STAMP
    )
    assert html.startswith("<!doctype html>")
    for fragment in (
        "Policy simulation: lending",
        "apps.csv",
        "2025-01-02 03:04 UTC",
        "Outcomes",
        "Rules",
        "Data quality",
        "Outcome rates by region",
        "never fired",
    ):
        assert fragment in html, fragment
    assert "<script" not in html and "http://" not in html and "https://" not in html


def test_diff_html_structure() -> None:
    html = render_diff_html(diff_result(), dataset="apps.csv", generated_at=STAMP)
    for fragment in (
        "Policy impact: lending",
        "Who moved where",
        "What drove the changes",
        "Changed records",
        "old policy",
        "new policy",
        "lending v1.0",
        "lending v1.0",
    ):
        assert fragment in html, fragment
    assert "<script" not in html and "https://" not in html


def test_html_escapes_untrusted_text() -> None:
    evil = "<img src=x onerror=alert(1)>"
    rows = [{**GOOD, "region": evil}] * 40
    policy = Policy.from_toml(
        LENDING.replace("Credit score {credit_score} is below 580", "<b>{credit_score}</b>")
    )
    sim = render_simulation_html(simulate(policy, rows, group_by="region"))
    diff = render_diff_html(
        diff_policies(policy, policy, rows, group_by="region", id_field="region")
    )
    for html in (sim, diff):
        assert evil not in html
        assert "&lt;img src=x onerror=alert(1)&gt;" in html
    changed = Policy.from_toml(
        LENDING.replace("credit_score < 580", "credit_score < 800").replace(
            "Credit score {credit_score} is below 580", "<b>{credit_score}</b>"
        )
    )
    html = render_diff_html(diff_policies(policy, changed, rows, id_field="region"))
    assert "<b>720</b>" not in html and "&lt;b&gt;720&lt;/b&gt;" in html
    assert evil not in html


def test_html_pages_declare_light_and_dark_themes() -> None:
    html = render_simulation_html(simulate(lending(), data()))
    assert "prefers-color-scheme:dark" in html
    assert re.search(r"<title>[^<]+</title>", html)
