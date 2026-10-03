from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from rulelens.cli import EXIT_ERROR, EXIT_FINDINGS, EXIT_GATE, EXIT_OK, main
from tests.helpers import LENDING

GOOD_JSON = '{"credit_score": 720, "income": 90000, "debt": 1000, "employed": true}'


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture
def policy_file(tmp_path: Path) -> Path:
    path = tmp_path / "p.toml"
    path.write_text(
        LENDING
        + '\n[[tests]]\nname = "ok"\ninput = { credit_score = 700, income = 90000, debt = 1000, employed = true }\nexpect = "approve"\n'
    )
    return path


@pytest.fixture
def data_file(tmp_path: Path) -> Path:
    path = tmp_path / "d.csv"
    rows = ["credit_score,income,debt,employed,region"]
    rows += (
        ["720,90000,1000,true,N"] * 35 + ["400,90000,1000,true,S"] * 35 + [",90000,1000,true,S"] * 3
    )
    path.write_text("\n".join(rows))
    return path


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0
    assert capsys.readouterr().out.startswith("rulelens ")


def test_lint_clean_and_json(capsys: pytest.CaptureFixture[str], policy_file: Path) -> None:
    code, out, _ = run(capsys, "lint", str(policy_file), "--color", "never")
    assert code == EXIT_OK and "I201" in out  # unused fields are informational, not failures
    code, out, _ = run(capsys, "lint", str(policy_file), "--format", "json")
    payload = json.loads(out)
    assert code == EXIT_OK and payload[0]["policy"] == str(policy_file)


def test_lint_fails_on_errors_and_strict_warnings(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, policy_file: Path
) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text(LENDING.replace("credit_score < 580", "credit_scor < 580"))
    code, out, _ = run(capsys, "lint", str(bad), "--color", "never")
    assert code == EXIT_FINDINGS and "did you mean 'credit_score'" in out

    warn = tmp_path / "warn.toml"
    warn.write_text(
        LENDING.replace("credit_score < 580", "credit_score < 580 and credit_score > 600")
    )
    assert run(capsys, "lint", str(warn))[0] == EXIT_OK
    assert run(capsys, "lint", str(warn), "--strict")[0] == EXIT_FINDINGS


def test_lint_reports_unreadable_policy(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    code, _, err = run(capsys, "lint", str(tmp_path / "missing.toml"))
    assert code == EXIT_ERROR and "cannot read policy file" in err
    broken = tmp_path / "broken.toml"
    broken.write_text("[policy")
    code, out, _ = run(capsys, "lint", str(broken))
    assert code == EXIT_FINDINGS and "E100" in out


def test_test_command(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, policy_file: Path
) -> None:
    code, out, _ = run(capsys, "test", str(policy_file), "--color", "never")
    assert code == EXIT_OK and "1/1 passed" in out
    code, out, _ = run(capsys, "test", str(policy_file), "--format", "json")
    assert json.loads(out)[0]["cases"][0]["passed"] is True

    failing = tmp_path / "f.toml"
    failing.write_text(
        LENDING
        + '\n[[tests]]\nname = "x"\ninput = { credit_score = 100, income = 90000, debt = 1000, employed = true }\nexpect = "approve"\n'
    )
    code, out, _ = run(capsys, "test", str(failing), "--color", "never")
    assert code == EXIT_FINDINGS and "✘ x" in out


def test_decide_input_forms(
    capsys: pytest.CaptureFixture[str], policy_file: Path, tmp_path: Path
) -> None:
    code, out, _ = run(capsys, "decide", str(policy_file), "--json", GOOD_JSON, "--color", "never")
    assert code == EXIT_OK and "Decision: APPROVE" in out

    code, out, _ = run(
        capsys,
        "decide",
        str(policy_file),
        "--set",
        "credit_score=500",
        "income=90000",
        "debt=1000",
        "employed=yes",
        "--brief",
        "--color",
        "never",
    )
    assert "DECLINE" in out and "Rules" not in out and "Credit score 500 is below 580" in out

    record = tmp_path / "r.json"
    record.write_text(GOOD_JSON)
    code, out, _ = run(
        capsys, "decide", str(policy_file), "--file", str(record), "--format", "json"
    )
    assert json.loads(out)["outcome"] == "approve"


def test_decide_colour_and_stdin(
    capsys: pytest.CaptureFixture[str], policy_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, out, _ = run(capsys, "decide", str(policy_file), "--json", GOOD_JSON, "--color", "always")
    assert "\x1b[" in out
    import io

    monkeypatch.setattr(sys, "stdin", io.StringIO(GOOD_JSON))
    code, out, _ = run(capsys, "decide", str(policy_file), "--file", "-", "--format", "json")
    assert code == EXIT_OK and json.loads(out)["outcome"] == "approve"


@pytest.mark.parametrize(
    "extra",
    [
        ["--json", "{nope"],
        ["--json", "[1]"],
        ["--set", "novalue"],
        ["--file", "/no/such/file.json"],
    ],
)
def test_decide_input_errors(
    capsys: pytest.CaptureFixture[str], policy_file: Path, extra: list[str]
) -> None:
    code, _, err = run(capsys, "decide", str(policy_file), *extra)
    assert code == EXIT_ERROR and err.startswith("error:")


def test_decide_strict_rejects_bad_values(
    capsys: pytest.CaptureFixture[str], policy_file: Path
) -> None:
    code, _, err = run(capsys, "decide", str(policy_file), "--set", "credit_score=abc", "--strict")
    assert code == EXIT_ERROR and "credit_score" in err


def test_simulate(
    capsys: pytest.CaptureFixture[str], policy_file: Path, data_file: Path, tmp_path: Path
) -> None:
    html = tmp_path / "out" / "sim.html"
    code, out, err = run(
        capsys,
        "simulate",
        str(policy_file),
        str(data_file),
        "--group-by",
        "region",
        "--html",
        str(html),
        "--min-group-size",
        "10",
        "--color",
        "never",
    )
    assert code == EXIT_OK and "Records 73" in out and "Outcome by region" in out
    assert html.read_text().startswith("<!doctype html>") and "written to" in err
    code, out, _ = run(capsys, "simulate", str(policy_file), str(data_file), "--format", "json")
    assert json.loads(out)["total"] == 73


def test_diff_and_gate(
    capsys: pytest.CaptureFixture[str], policy_file: Path, data_file: Path, tmp_path: Path
) -> None:
    stricter = tmp_path / "v2.toml"
    stricter.write_text(LENDING.replace("580", "730").replace('version = "1.0"', 'version = "2.0"'))
    html = tmp_path / "diff.html"
    code, out, _ = run(
        capsys,
        "diff",
        str(policy_file),
        str(stricter),
        str(data_file),
        "--html",
        str(html),
        "--color",
        "never",
    )
    assert code == EXIT_OK and "Replayed 73 records" in out and html.exists()

    code, out, _ = run(
        capsys, "diff", str(policy_file), str(stricter), str(data_file), "--format", "json"
    )
    assert json.loads(out)["changed"] == 35

    code, _, err = run(
        capsys, "diff", str(policy_file), str(stricter), str(data_file), "--max-change-rate", "0.1"
    )
    assert code == EXIT_GATE and "policy gate failed" in err
    code, _, _ = run(
        capsys, "diff", str(policy_file), str(stricter), str(data_file), "--max-change-rate", "0.9"
    )
    assert code == EXIT_OK
    code, _, _ = run(
        capsys, "diff", str(policy_file), str(policy_file), str(data_file), "--max-change-rate", "0"
    )
    assert code == EXIT_OK


def test_data_errors_exit_2(
    capsys: pytest.CaptureFixture[str], policy_file: Path, tmp_path: Path
) -> None:
    code, _, err = run(capsys, "simulate", str(policy_file), str(tmp_path / "nope.csv"))
    assert code == EXIT_ERROR and "not found" in err


def test_python_dash_m_entry_point(policy_file: Path) -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "rulelens",
            "decide",
            str(policy_file),
            "--json",
            GOOD_JSON,
            "--brief",
            "--color",
            "never",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0 and "APPROVE" in proc.stdout
