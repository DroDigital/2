"""Command-line interface: ``rulelens lint | test | decide | simulate | diff``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from . import __version__
from .analysis import lint as lint_policy
from .dataio import read_records
from .diagnostics import Diagnostic, has_errors
from .diff import diff_policies
from .errors import DataError, RuleLensError
from .html_report import render_diff_html, render_simulation_html
from .policy import Policy, load_with_diagnostics
from .report import (
    Style,
    diff_to_dict,
    render_diff,
    render_lint,
    render_simulation,
    render_tests,
    simulation_to_dict,
)
from .simulate import simulate
from .testing import run_tests

EXIT_OK: Final = 0
EXIT_FINDINGS: Final = 1  # lint errors / failed tests
EXIT_ERROR: Final = 2  # bad usage, unreadable or invalid input
EXIT_GATE: Final = 3  # `diff --max-change-rate` exceeded


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rulelens",
        description="Explainable business-rule decisions with static analysis and impact diffing.",
    )
    parser.add_argument("--version", action="version", version=f"rulelens {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="colour output (default: auto)",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    p = sub.add_parser(
        "lint", parents=[common], help="validate policies and report rules that can never matter"
    )
    p.add_argument("policies", nargs="+", type=Path, metavar="POLICY")
    p.add_argument("--strict", action="store_true", help="treat warnings as failures")
    p.add_argument("--format", choices=("text", "json"), default="text")

    p = sub.add_parser("test", parents=[common], help="run the [[tests]] embedded in policies")
    p.add_argument("policies", nargs="+", type=Path, metavar="POLICY")
    p.add_argument("--format", choices=("text", "json"), default="text")

    p = sub.add_parser("decide", parents=[common], help="decide a single record and explain why")
    p.add_argument("policy", type=Path)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--json", dest="json_text", metavar="OBJECT", help="record as a JSON object")
    src.add_argument(
        "--file", type=Path, metavar="PATH", help="JSON file with the record ('-' for stdin)"
    )
    src.add_argument(
        "--set", dest="pairs", nargs="+", metavar="FIELD=VALUE", help="record as field=value pairs"
    )
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.add_argument("--brief", action="store_true", help="outcome and reasons only")
    p.add_argument("--strict", action="store_true", help="fail on uncoercible values")

    p = sub.add_parser("simulate", parents=[common], help="replay a data file through a policy")
    p.add_argument("policy", type=Path)
    p.add_argument("data", type=Path, metavar="DATA", help=".csv, .jsonl or .json")
    _add_report_options(p)

    p = sub.add_parser(
        "diff",
        parents=[common],
        help="show how a new policy version changes decisions on real data",
    )
    p.add_argument("old", type=Path, metavar="OLD_POLICY")
    p.add_argument("new", type=Path, metavar="NEW_POLICY")
    p.add_argument("data", type=Path, metavar="DATA", help=".csv, .jsonl or .json")
    p.add_argument(
        "--id-field", metavar="COLUMN", help="column that identifies a record in examples"
    )
    p.add_argument(
        "--examples",
        type=int,
        default=3,
        metavar="N",
        help="changed records to show per transition",
    )
    p.add_argument(
        "--max-change-rate",
        type=float,
        metavar="FRACTION",
        help="exit with status 3 if more than this fraction of records change outcome (a CI gate)",
    )
    _add_report_options(p)
    return parser


def _add_report_options(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--group-by", metavar="COLUMN", help="audit outcome rates per value of this column"
    )
    p.add_argument("--min-group-size", type=int, default=30, metavar="N")
    p.add_argument(
        "--html", type=Path, metavar="FILE", help="also write a self-contained HTML report"
    )
    p.add_argument("--format", choices=("text", "json"), default="text")


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns the process exit status."""
    args = build_parser().parse_args(argv)
    style = Style(_use_color(args.color))
    handlers = {
        "lint": _cmd_lint,
        "test": _cmd_test,
        "decide": _cmd_decide,
        "simulate": _cmd_simulate,
        "diff": _cmd_diff,
    }
    try:
        return handlers[args.command](args, style)
    except RuleLensError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


def _use_color(mode: str) -> bool:
    if mode == "always":
        return True
    return mode == "auto" and sys.stdout.isatty() and "NO_COLOR" not in os.environ


def _emit_json(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


# -- commands -------------------------------------------------------------------------------


def _cmd_lint(args: argparse.Namespace, style: Style) -> int:
    failed = False
    payload: list[dict[str, Any]] = []
    for path in args.policies:
        policy, diagnostics = load_with_diagnostics(path)
        if policy is not None:
            diagnostics = sorted([*diagnostics, *lint_policy(policy)], key=Diagnostic.sort_key)
        problems = has_errors(diagnostics) or (
            args.strict and any(d.severity == "warning" for d in diagnostics)
        )
        failed = failed or problems
        if args.format == "json":
            payload.append({"policy": str(path), "diagnostics": [d.to_dict() for d in diagnostics]})
        else:
            print(render_lint(str(path), diagnostics, style))
    if args.format == "json":
        _emit_json(payload)
    return EXIT_FINDINGS if failed else EXIT_OK


def _cmd_test(args: argparse.Namespace, style: Style) -> int:
    failed = False
    payload: list[dict[str, Any]] = []
    for path in args.policies:
        results = run_tests(Policy.load(path))
        failed = failed or any(not r.passed for r in results)
        if args.format == "json":
            payload.append(
                {
                    "policy": str(path),
                    "cases": [
                        {"name": r.name, "passed": r.passed, "failures": list(r.failures)}
                        for r in results
                    ],
                }
            )
        else:
            print(render_tests(str(path), results, style))
    if args.format == "json":
        _emit_json(payload)
    return EXIT_FINDINGS if failed else EXIT_OK


def _read_record(args: argparse.Namespace) -> dict[str, Any]:
    try:
        if args.json_text is not None:
            data = json.loads(args.json_text)
        elif args.file is not None:
            text = (
                sys.stdin.read() if str(args.file) == "-" else args.file.read_text(encoding="utf-8")
            )
            data = json.loads(text)
        else:
            data = {}
            for pair in args.pairs:
                key, sep, value = pair.partition("=")
                if not sep or not key:
                    raise DataError(f"expected FIELD=VALUE, got {pair!r}")
                data[key] = value
    except json.JSONDecodeError as exc:
        raise DataError(f"record is not valid JSON: {exc.msg}") from exc
    except OSError as exc:
        raise DataError(f"cannot read record: {exc}") from exc
    if not isinstance(data, dict):
        raise DataError("record must be a JSON object")
    return data


def _cmd_decide(args: argparse.Namespace, style: Style) -> int:
    policy = Policy.load(args.policy)
    decision = policy.decide(_read_record(args), trace=True, strict=args.strict)
    if args.format == "json":
        _emit_json(decision.to_dict())
    else:
        rank = policy.severity(decision.outcome)
        text = decision.explain(verbose=not args.brief)
        head, _, rest = text.partition("\n")
        colored = head.replace(
            decision.outcome.upper(),
            style.outcome(decision.outcome.upper(), rank, len(policy.outcomes)),
            1,
        )
        print(colored + ("\n" + rest if rest else ""))
    return EXIT_OK


def _stamp() -> datetime:
    return datetime.now(UTC)


def _write_html(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"HTML report written to {path}", file=sys.stderr)


def _cmd_simulate(args: argparse.Namespace, style: Style) -> int:
    policy = Policy.load(args.policy)
    result = simulate(
        policy, read_records(args.data), group_by=args.group_by, min_group_size=args.min_group_size
    )
    if args.format == "json":
        _emit_json(simulation_to_dict(result))
    else:
        print(render_simulation(result, style))
    if args.html:
        _write_html(
            args.html, render_simulation_html(result, dataset=args.data.name, generated_at=_stamp())
        )
    return EXIT_OK


def _cmd_diff(args: argparse.Namespace, style: Style) -> int:
    old, new = Policy.load(args.old), Policy.load(args.new)
    result = diff_policies(
        old,
        new,
        read_records(args.data),
        group_by=args.group_by,
        id_field=args.id_field,
        max_examples=args.examples,
        min_group_size=args.min_group_size,
    )
    if args.format == "json":
        _emit_json(diff_to_dict(result))
    else:
        print(render_diff(result, style))
    if args.html:
        _write_html(
            args.html, render_diff_html(result, dataset=args.data.name, generated_at=_stamp())
        )
    limit = args.max_change_rate
    if limit is not None and result.changed_rate > limit:
        print(
            f"policy gate failed: {result.changed_rate:.1%} of records changed outcome "
            f"(limit {limit:.1%})",
            file=sys.stderr,
        )
        return EXIT_GATE
    return EXIT_OK
