"""Plain-text renderers for the command line."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .decision import PolicyRef
from .diagnostics import Diagnostic
from .diff import DiffResult
from .simulate import GroupAudit, SimulationResult
from .testing import CaseResult

BAR_WIDTH = 24


class Style:
    """ANSI styling that degrades to plain text when disabled."""

    def __init__(self, color: bool = False) -> None:
        self.color = color

    def _wrap(self, code: str, text: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if self.color else text

    def bold(self, text: str) -> str:
        return self._wrap("1", text)

    def dim(self, text: str) -> str:
        return self._wrap("2", text)

    def red(self, text: str) -> str:
        return self._wrap("31", text)

    def green(self, text: str) -> str:
        return self._wrap("32", text)

    def yellow(self, text: str) -> str:
        return self._wrap("33", text)

    def outcome(self, name: str, rank: int, count: int) -> str:
        """Colour an outcome by severity: least severe green, most severe red."""
        if count <= 1 or rank == 0:
            return self.green(name)
        return self.red(name) if rank == count - 1 else self.yellow(name)


def bar(fraction: float, width: int = BAR_WIDTH) -> str:
    filled = round(max(0.0, min(1.0, fraction)) * width)
    return "█" * filled + "░" * (width - filled)


def pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "–"


def plural(n: int, word: str) -> str:
    return f"{n:,} {word}" if n == 1 else f"{n:,} {word}s"


# -- lint -----------------------------------------------------------------------------------


def render_lint(path: str, diagnostics: Sequence[Diagnostic], style: Style) -> str:
    counts = {
        s: sum(1 for d in diagnostics if d.severity == s) for s in ("error", "warning", "info")
    }
    if not diagnostics:
        return f"{path}: {style.green('no problems found')}"
    summary = ", ".join(plural(counts[s], s) for s in ("error", "warning", "info") if counts[s])
    lines = [f"{style.bold(path)}: {summary}"]
    painters = {"error": style.red, "warning": style.yellow, "info": style.dim}
    for d in diagnostics:
        where = f" {d.location}:" if d.location else ""
        lines.append(f"  {painters[d.severity](f'{d.severity} {d.code}')}{where} {d.message}")
        if d.snippet:
            lines.extend(f"  {line}" for line in d.snippet.splitlines()[1:])
    return "\n".join(lines)


# -- policy tests ---------------------------------------------------------------------------


def render_tests(path: str, results: Sequence[CaseResult], style: Style) -> str:
    if not results:
        return f"{path}: {style.yellow('no [[tests]] defined')}"
    failed = [r for r in results if not r.passed]
    lines = [style.bold(path)]
    for r in results:
        if r.passed:
            lines.append(f"  {style.green('✔')} {r.name}")
        else:
            lines.append(f"  {style.red('✘')} {r.name}")
            lines.extend(f"      {f}" for f in r.failures)
            lines.extend(f"      {ln}" for ln in r.decision.explain().splitlines())
    verdict = style.red(f"{len(failed)} failed") if failed else style.green("all passed")
    lines.append(f"  {len(results) - len(failed)}/{len(results)} passed ({verdict})")
    return "\n".join(lines)


# -- simulation -----------------------------------------------------------------------------


def _outcome_table(
    outcomes: Mapping[str, int], order: Sequence[str], total: int, style: Style
) -> list[str]:
    width = max((len(o) for o in order), default=0)
    lines = []
    for rank, name in enumerate(order):
        n = outcomes.get(name, 0)
        label = style.outcome(name.ljust(width), rank, len(order))
        lines.append(f"  {label}  {n:>7,}  {pct(n, total):>6}  {bar(n / total if total else 0)}")
    return lines


def _group_lines(audit: GroupAudit, title: str = "", note: bool = True) -> list[str]:
    head = f"Outcome by {audit.column}{title} (favorable outcome: {audit.favorable_outcome})"
    lines = [head]
    width = max((len(r.group) for r in audit.rows), default=0)
    for row in audit.rows:
        ratio = "      –" if row.ratio is None else f"{row.ratio:7.3f}"
        flag = "  ⚠ below 0.80 of best group" if row.flagged else ""
        lines.append(
            f"  {row.group.ljust(width)}  n={row.n:<6,} favorable {100 * row.favorable_rate:5.1f}%  "
            f"ratio {ratio}{flag}"
        )
    if note:
        lines.append(
            f"  Groups under {audit.min_group_size} records are never flagged. "
            "This is a screening heuristic, not a legal or statistical finding."
        )
    return lines


def render_simulation(result: SimulationResult, style: Style) -> str:
    order = list(result.outcomes)
    lines = [
        f"{style.bold('Policy')}  {result.policy}",
        f"{style.bold('Records')} {result.total:,}",
        "",
        style.bold("Outcomes"),
        *_outcome_table(result.outcomes, order, result.total, style),
    ]
    if result.defaulted:
        lines.append(
            f"  {plural(result.defaulted, 'record')} fell through to the default ({pct(result.defaulted, result.total)})"
        )
    if result.escalated:
        lines.append(f"  {plural(result.escalated, 'record')} escalated because of missing data")

    lines += [
        "",
        style.bold("Rules"),
        "  rule                           outcome     fired  decisive  unknown",
    ]
    for r in result.rules:
        lines.append(
            f"  {r.rule_id:<30} {r.outcome:<10} {r.fired:>6,} {r.decisive:>9,} {r.unknown:>8,}"
        )
    if result.never_fired:
        lines.append(
            f"  {style.yellow('Never fired on this data:')} {', '.join(result.never_fired)}"
        )

    if result.missing or result.with_warnings:
        lines += ["", style.bold("Data quality")]
        for name, n in sorted(result.missing.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {name} is missing in {n:,} records ({pct(n, result.total)})")
        if result.with_warnings:
            lines.append(
                f"  {plural(result.with_warnings, 'record')} had value problems, e.g. {result.warning_samples[0]}"
            )
    if result.groups is not None:
        lines += ["", *_group_lines(result.groups)]
    return "\n".join(lines)


# -- diff -----------------------------------------------------------------------------------


def _vocabulary(result: DiffResult) -> list[str]:
    return list(result.old_outcomes) + [
        o for o in result.new_outcomes if o not in result.old_outcomes
    ]


def render_diff(result: DiffResult, style: Style) -> str:
    total = result.total
    order = _vocabulary(result)
    width = max((len(o) for o in order), default=0)
    lines = [
        style.bold("Policy impact"),
        f"  old  {result.old}",
        f"  new  {result.new}",
        "",
        f"Replayed {total:,} records: "
        f"{style.bold(f'{result.changed:,} changed outcome ({pct(result.changed, total)})')}"
        + (
            f", {result.rationale_changed:,} kept their outcome for different reasons"
            if result.rationale_changed
            else ""
        ),
        "",
        style.bold("Outcomes"),
        f"  {'':<{width}}  {'old':>7}  {'new':>7}  {'change':>7}",
    ]
    for rank, name in enumerate(order):
        before, after = result.old_counts.get(name, 0), result.new_counts.get(name, 0)
        delta = after - before
        shown = f"{delta:+,}" if delta else "0"
        paint = style.green if delta == 0 else style.yellow
        label = style.outcome(name.ljust(width), rank, len(order))
        lines.append(f"  {label}  {before:>7,}  {after:>7,}  {paint(f'{shown:>7}')}")

    moved = sorted(
        ((k, v) for k, v in result.transitions.items() if k[0] != k[1]), key=lambda kv: -kv[1]
    )
    if moved:
        lines += ["", style.bold("Transitions (old → new)")]
        for (src, dst), n in moved:
            lines.append(
                f"  {src:>{width}} → {dst:<{width}}  {n:>6,}  {pct(n, total):>6}  {bar(n / total)}"
            )
        lines += ["", style.bold("What drove the changes (deciding rule: old → new)")]
        for d in result.drivers[:10]:
            lines.append(f"  {d.old} → {d.new}: {d.count:,}")

    if result.examples:
        lines += ["", style.bold("Examples")]
        for ex in result.examples:
            ident = f" (id={ex.record_id})" if ex.record_id else ""
            lines.append(f"  row {ex.index}{ident}: {ex.old_outcome} → {ex.new_outcome}")
            lines.append(f"      before: {'; '.join(ex.old_reasons)}")
            lines.append(f"      after:  {'; '.join(ex.new_reasons)}")

    for label, missing in (("old", result.old_missing), ("new", result.new_missing)):
        for name, n in sorted(missing.items(), key=lambda kv: -kv[1]):
            if n * 10 >= total:  # only mention fields blank in at least 10% of the data
                lines.append(
                    style.yellow(
                        f"  ! {label} policy reads '{name}', unknown in {n:,} records ({pct(n, total)})"
                    )
                )

    for label, audit in (("old", result.old_groups), ("new", result.new_groups)):
        if audit is not None:
            lines += ["", *_group_lines(audit, f" — {label} policy", note=label == "new")]
    return "\n".join(lines)


# -- machine-readable forms -----------------------------------------------------------------


def simulation_to_dict(result: SimulationResult) -> dict[str, object]:
    data: dict[str, object] = {
        "policy": {
            "name": result.policy.name,
            "version": result.policy.version,
            "fingerprint": result.policy.fingerprint,
        },
        "total": result.total,
        "outcomes": dict(result.outcomes),
        "defaulted": result.defaulted,
        "escalated": result.escalated,
        "records_with_warnings": result.with_warnings,
        "missing": dict(result.missing),
        "never_fired": list(result.never_fired),
        "rules": [
            {
                "id": r.rule_id,
                "outcome": r.outcome,
                "fired": r.fired,
                "decisive": r.decisive,
                "unknown": r.unknown,
            }
            for r in result.rules
        ],
    }
    if result.groups is not None:
        data["groups"] = _audit_to_dict(result.groups)
    return data


def diff_to_dict(result: DiffResult) -> dict[str, object]:
    data: dict[str, object] = {
        "old": _ref_to_dict(result.old),
        "new": _ref_to_dict(result.new),
        "total": result.total,
        "changed": result.changed,
        "changed_rate": result.changed_rate,
        "rationale_changed": result.rationale_changed,
        "old_counts": dict(result.old_counts),
        "new_counts": dict(result.new_counts),
        "transitions": [
            {"old": a, "new": b, "count": n} for (a, b), n in sorted(result.transitions.items())
        ],
        "drivers": [{"old": d.old, "new": d.new, "count": d.count} for d in result.drivers],
        "examples": [
            {
                "row": e.index,
                "id": e.record_id,
                "old_outcome": e.old_outcome,
                "new_outcome": e.new_outcome,
                "old_reasons": list(e.old_reasons),
                "new_reasons": list(e.new_reasons),
            }
            for e in result.examples
        ],
        "old_missing": dict(result.old_missing),
        "new_missing": dict(result.new_missing),
    }
    if result.old_groups is not None and result.new_groups is not None:
        data["old_groups"] = _audit_to_dict(result.old_groups)
        data["new_groups"] = _audit_to_dict(result.new_groups)
    return data


def _ref_to_dict(ref: PolicyRef) -> dict[str, str]:
    return {"name": ref.name, "version": ref.version, "fingerprint": ref.fingerprint}


def _audit_to_dict(audit: GroupAudit) -> dict[str, object]:
    return {
        "column": audit.column,
        "favorable_outcome": audit.favorable_outcome,
        "reference": audit.reference,
        "rows": [
            {
                "group": r.group,
                "n": r.n,
                "favorable_rate": r.favorable_rate,
                "ratio": r.ratio,
                "flagged": r.flagged,
            }
            for r in audit.rows
        ],
    }
