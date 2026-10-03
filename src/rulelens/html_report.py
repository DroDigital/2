"""Self-contained HTML reports for ``simulate`` and ``diff``.

One file, no JavaScript, no external requests: safe to attach to a change
ticket or open from an email. All dynamic text is HTML-escaped.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from html import escape as esc

from .diff import DiffResult
from .simulate import GroupAudit, SimulationResult

_CSS = """
:root{--bg:#f7f8fa;--card:#fff;--ink:#16181d;--muted:#5d6472;--line:#dfe3ea;--accent:#2f5bd6;
--warn:#a35a00;--warn-bg:#fff4e0;--sev-l:40%;--track:#e8ebf1}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--ink:#e8eaee;--muted:#9aa2b1;
--line:#2a2f3a;--accent:#7b9bff;--warn:#ffb454;--warn-bg:#2b2112;--sev-l:58%;--track:#262b36}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1040px;margin:0 auto;padding:28px 16px 64px}
h1{font-size:1.5rem;margin:0 0 4px}h2{font-size:1.1rem;margin:0 0 12px}
.sub{color:var(--muted);margin:0 0 24px;font-size:.9rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px 20px;margin:0 0 18px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:0 0 18px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.tile b{display:block;font-size:1.6rem;line-height:1.2}.tile span{color:var(--muted);font-size:.82rem}
table{width:100%;border-collapse:collapse;font-size:.92rem}
th,td{padding:7px 10px;text-align:left;border-bottom:1px solid var(--line);vertical-align:middle}
th{font-weight:600;color:var(--muted);font-size:.78rem;text-transform:uppercase;letter-spacing:.04em}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.bar{height:10px;background:var(--track);border-radius:5px;overflow:hidden;min-width:90px}
.bar>i{display:block;height:100%;background:var(--accent)}
.chip{display:inline-block;padding:1px 9px;border-radius:99px;font-size:.8rem;font-weight:600;color:#fff;
background:hsl(var(--h) 55% var(--sev-l))}
.hm td{text-align:center;font-variant-numeric:tabular-nums;
background:color-mix(in srgb,var(--accent) calc(var(--a,0)*70%),transparent)}
.hm td.diag{color:var(--muted)}.hm th:first-child{text-align:left}
.note{background:var(--warn-bg);color:var(--warn);border-radius:8px;padding:8px 12px;margin:10px 0 0;font-size:.88rem}
.flag{color:var(--warn);font-weight:600}.muted{color:var(--muted)}
code{font:.88em ui-monospace,SFMono-Regular,Menlo,monospace}
details{border-top:1px solid var(--line);padding:8px 0}summary{cursor:pointer}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media (max-width:640px){.cols{grid-template-columns:1fr}.tile b{font-size:1.3rem}}
@media print{body{background:#fff}.card,.tile{break-inside:avoid}}
"""


def _page(title: str, subtitle: str, body: str) -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{esc(title)}</title><style>{_CSS}</style></head><body><main>"
        f'<h1>{esc(title)}</h1><p class="sub">{subtitle}</p>{body}</main></body></html>'
    )


def _hue(rank: int, count: int) -> int:
    """Green for the least severe outcome through to red for the most severe."""
    return 140 if count <= 1 else round(140 - 135 * rank / (count - 1))


def _chip(name: str, order: Sequence[str]) -> str:
    rank = order.index(name) if name in order else 0
    return f'<span class="chip" style="--h:{_hue(rank, len(order))}">{esc(name)}</span>'


def _tile(value: str, label: str) -> str:
    return f'<div class="tile"><b>{esc(value)}</b><span>{esc(label)}</span></div>'


def _bar(fraction: float) -> str:
    width = max(0.0, min(1.0, fraction)) * 100
    return f'<div class="bar"><i style="width:{width:.1f}%"></i></div>'


def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "–"


def _outcome_rows(counts: Mapping[str, int], order: Sequence[str], total: int) -> str:
    rows = []
    for name in order:
        n = counts.get(name, 0)
        rows.append(
            f'<tr><td>{_chip(name, order)}</td><td class="n">{n:,}</td>'
            f'<td class="n">{_pct(n, total)}</td><td>{_bar(n / total if total else 0)}</td></tr>'
        )
    return "".join(rows)


def _groups(audit: GroupAudit, heading: str) -> str:
    rows = []
    for r in audit.rows:
        ratio = "–" if r.ratio is None else f"{r.ratio:.3f}"
        flag = '<span class="flag">⚠ below 0.80</span>' if r.flagged else ""
        rows.append(
            f"<tr><td>{esc(r.group)}</td><td class='n'>{r.n:,}</td>"
            f"<td class='n'>{100 * r.favorable_rate:.1f}%</td>"
            f"<td class='n'>{ratio}</td><td>{flag}</td></tr>"
        )
    return (
        f'<div class="card"><h2>{esc(heading)}</h2>'
        "<table><tr><th>Group</th><th class='n'>Records</th>"
        f"<th class='n'>“{esc(audit.favorable_outcome)}” rate</th><th class='n'>Ratio</th><th></th></tr>"
        f"{''.join(rows)}</table>"
        f'<p class="muted">Ratio is each group’s favorable rate divided by the best group’s. '
        f"Groups under {audit.min_group_size} records are never flagged. This is a screening "
        "heuristic (“four-fifths rule”), not a legal or statistical finding.</p></div>"
    )


def _stamp(generated_at: datetime | None) -> str:
    return esc(generated_at.strftime("%Y-%m-%d %H:%M UTC")) if generated_at else ""


def render_simulation_html(
    result: SimulationResult, *, dataset: str = "", generated_at: datetime | None = None
) -> str:
    order = list(result.outcomes)
    total = result.total
    sub = f"{esc(str(result.policy))} · {total:,} records"
    if dataset:
        sub += f" · {esc(dataset)}"
    if generated_at:
        sub += f" · {_stamp(generated_at)}"
    tiles = [
        _tile(f"{total:,}", "records replayed"),
        _tile(f"{result.defaulted:,}", "fell through to default"),
        _tile(f"{len(result.never_fired)}", "rules that never fired"),
        _tile(f"{sum(result.missing.values()):,}", "missing values in rule inputs"),
    ]
    rules = "".join(
        f"<tr><td><code>{esc(r.rule_id)}</code></td><td>{_chip(r.outcome, order)}</td>"
        f"<td class='n'>{r.fired:,}</td><td class='n'>{r.decisive:,}</td><td class='n'>{r.unknown:,}</td></tr>"
        for r in result.rules
    )
    body = [
        f'<div class="tiles">{"".join(tiles)}</div>',
        '<div class="card"><h2>Outcomes</h2><table><tr><th>Outcome</th><th class="n">Records</th>'
        '<th class="n">Share</th><th></th></tr>'
        f"{_outcome_rows(result.outcomes, order, total)}</table></div>",
        '<div class="card"><h2>Rules</h2><table><tr><th>Rule</th><th>Outcome</th><th class="n">Fired</th>'
        '<th class="n">Decisive</th><th class="n">Unknown</th></tr>' + rules + "</table>",
    ]
    if result.never_fired:
        names = ", ".join(f"<code>{esc(n)}</code>" for n in result.never_fired)
        body.append(f'<div class="note">Never fired on this data: {names}</div>')
    body.append("</div>")
    if result.missing:
        items = "".join(
            f"<tr><td><code>{esc(k)}</code></td><td class='n'>{v:,}</td><td class='n'>{_pct(v, total)}</td></tr>"
            for k, v in sorted(result.missing.items(), key=lambda kv: -kv[1])
        )
        body.append(
            '<div class="card"><h2>Data quality</h2><table><tr><th>Field</th><th class="n">Missing</th>'
            f'<th class="n">Share</th></tr>{items}</table></div>'
        )
    if result.groups is not None:
        body.append(_groups(result.groups, f"Outcome rates by {result.groups.column}"))
    return _page(f"Policy simulation: {result.policy.name}", sub, "".join(body))


def _matrix(result: DiffResult, order: Sequence[str]) -> str:
    off_diag = [v for (a, b), v in result.transitions.items() if a != b]
    peak = max(off_diag, default=0) or 1
    head = "".join(f"<th>{_chip(n, order)}</th>" for n in order)
    rows = []
    for old_name in order:
        cells = []
        for new_name in order:
            n = result.transitions.get((old_name, new_name), 0)
            if old_name == new_name:
                cells.append(f'<td class="diag">{n:,}</td>')
            else:
                cells.append(f'<td style="--a:{n / peak:.2f}">{n:,}</td>' if n else "<td>·</td>")
        rows.append(f"<tr><th>{_chip(old_name, order)}</th>{''.join(cells)}</tr>")
    return f'<table class="hm"><tr><th>old ↓ &nbsp; new →</th>{head}</tr>{"".join(rows)}</table>'


def render_diff_html(
    result: DiffResult, *, dataset: str = "", generated_at: datetime | None = None
) -> str:
    order = list(result.old_outcomes) + [
        o for o in result.new_outcomes if o not in result.old_outcomes
    ]
    total = result.total
    sub = f"{esc(str(result.old))} → {esc(str(result.new))} · {total:,} records"
    if dataset:
        sub += f" · {esc(dataset)}"
    if generated_at:
        sub += f" · {_stamp(generated_at)}"
    tiles = [
        _tile(f"{total:,}", "records replayed"),
        _tile(f"{result.changed:,}", "changed outcome"),
        _tile(_pct(result.changed, total), "change rate"),
        _tile(f"{result.rationale_changed:,}", "same outcome, different rule"),
    ]
    shift_rows = []
    for name in order:
        before, after = result.old_counts.get(name, 0), result.new_counts.get(name, 0)
        delta = after - before
        shift_rows.append(
            f"<tr><td>{_chip(name, order)}</td><td class='n'>{before:,}</td><td class='n'>{after:,}</td>"
            f"<td class='n'>{delta:+,}</td><td>{_bar(after / total if total else 0)}</td></tr>"
        )
    body = [
        f'<div class="tiles">{"".join(tiles)}</div>',
        '<div class="card"><h2>Outcome shift</h2><table><tr><th>Outcome</th><th class="n">Old</th>'
        '<th class="n">New</th><th class="n">Change</th><th>New share</th></tr>'
        f"{''.join(shift_rows)}</table></div>",
        f'<div class="card"><h2>Who moved where</h2>{_matrix(result, order)}</div>',
    ]
    if result.drivers:
        drivers = "".join(
            f"<tr><td><code>{esc(d.old)}</code></td><td><code>{esc(d.new)}</code></td>"
            f"<td class='n'>{d.count:,}</td><td>{_bar(d.count / result.changed)}</td></tr>"
            for d in result.drivers[:12]
        )
        body.append(
            '<div class="card"><h2>What drove the changes</h2><table><tr><th>Deciding rule before</th>'
            f'<th>Deciding rule now</th><th class="n">Records</th><th></th></tr>{drivers}</table></div>'
        )
    if result.examples:
        items = []
        for ex in result.examples:
            ident = f" · id {esc(ex.record_id)}" if ex.record_id else ""
            items.append(
                f"<details><summary>Row {ex.index}{ident}: {_chip(ex.old_outcome, order)} → "
                f"{_chip(ex.new_outcome, order)}</summary><div class='cols'>"
                f"<div><b>Before</b><br>{'<br>'.join(esc(r) for r in ex.old_reasons)}</div>"
                f"<div><b>After</b><br>{'<br>'.join(esc(r) for r in ex.new_reasons)}</div></div></details>"
            )
        body.append(f'<div class="card"><h2>Changed records</h2>{"".join(items)}</div>')
    notes = [
        f"The {label} policy reads <code>{esc(name)}</code>, which is unknown in {n:,} records ({_pct(n, total)})."
        for label, missing in (("old", result.old_missing), ("new", result.new_missing))
        for name, n in sorted(missing.items(), key=lambda kv: -kv[1])
        if n * 10 >= total
    ]
    if notes:
        body.append(
            '<div class="card"><h2>Data coverage</h2>'
            + "".join(f'<div class="note">{n}</div>' for n in notes)
            + "</div>"
        )
    for label, audit in (("old", result.old_groups), ("new", result.new_groups)):
        if audit is not None:
            body.append(_groups(audit, f"Outcome rates by {audit.column}: {label} policy"))
    return _page(f"Policy impact: {result.new.name}", sub, "".join(body))
