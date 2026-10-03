"""Policy files: loading, validation and decision making.

A policy is a TOML document::

    [policy]            name, version, strategy, outcomes, default, on_unknown
    [schema]            field name -> int | number | string | bool | date
    [derived]           computed fields, each an expression over earlier fields
    [[rules]]           id, when, outcome, reason
    [[tests]]           embedded regression cases (see ``rulelens test``)

Validation is exhaustive: every problem is reported as a :class:`Diagnostic`
instead of stopping at the first, so one ``lint`` run shows everything to fix.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from .decision import Decision, PolicyRef, RuleResult, display
from .diagnostics import ERROR, WARNING, Diagnostic, has_errors
from .errors import DataError, ExpressionError, PolicyError
from .evaluator import evaluate
from .nodes import Node, Value, referenced_fields, to_source
from .parser import parse
from .schema import DECLARED_TYPES, coerce, flatten_schema, lookup, static_type
from .trace import build_clause
from .typecheck import typecheck

STRATEGIES: Final = ("first_match", "most_severe")

_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.\-]*$")
_PLACEHOLDER = re.compile(r"\{\{|\}\}|\{([A-Za-z_][A-Za-z0-9_.]*)(?::([^{}]*))?\}")
_FORMAT_SPEC = re.compile(r"[0-9A-Za-z.,%+\-<>^=_ :/]{0,16}$")

_TOP_KEYS: Final = {"policy", "schema", "derived", "rules", "tests"}
_POLICY_KEYS: Final = {
    "name",
    "version",
    "description",
    "strategy",
    "outcomes",
    "default",
    "on_unknown",
}
_RULE_KEYS: Final = {"id", "when", "outcome", "reason", "description", "tags"}
_TEST_KEYS: Final = {"name", "input", "expect", "expect_rules"}


@dataclass(frozen=True, slots=True)
class Rule:
    id: str
    when: str
    expr: Node
    outcome: str
    reason: str
    description: str = ""
    tags: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()  # schema fields this rule depends on, through derived fields too


@dataclass(frozen=True, slots=True)
class Derived:
    name: str
    source: str
    expr: Node
    type: str


@dataclass(frozen=True, slots=True)
class PolicyTest:
    name: str
    input: dict[str, Any]
    expect: str
    expect_rules: tuple[str, ...] | None = None


@dataclass(frozen=True, eq=False)
class Policy:
    """A validated, ready-to-run policy. Create one with :meth:`load`."""

    name: str
    version: str
    strategy: str
    outcomes: tuple[str, ...]  # ordered from least to most severe
    default: str
    schema: Mapping[str, str]
    derived: tuple[Derived, ...]
    rules: tuple[Rule, ...]
    fingerprint: str
    description: str = ""
    on_unknown: str | None = None
    tests: tuple[PolicyTest, ...] = ()
    origin: str | None = None
    _severity: dict[str, int] = field(default_factory=dict, repr=False)
    _by_id: dict[str, Rule] = field(default_factory=dict, repr=False)

    # -- construction ---------------------------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path) -> Policy:
        """Read and validate a TOML policy file.

        Raises:
            PolicyError: if the file is unreadable or has any validation error.
        """
        policy, diagnostics = load_with_diagnostics(path)
        return _require(policy, diagnostics, str(path))

    @classmethod
    def from_toml(cls, text: str, origin: str | None = None) -> Policy:
        policy, diagnostics = build_policy(_parse_toml(text), origin)
        return _require(policy, diagnostics, origin or "<string>")

    @classmethod
    def from_dict(cls, data: dict[str, Any], origin: str | None = None) -> Policy:
        policy, diagnostics = build_policy(data, origin)
        return _require(policy, diagnostics, origin or "<dict>")

    @property
    def ref(self) -> PolicyRef:
        return PolicyRef(self.name, self.version, self.fingerprint)

    def severity(self, outcome: str) -> int:
        """Rank of ``outcome``; higher is more severe."""
        return self._severity[outcome]

    def rule(self, rule_id: str) -> Rule:
        return self._by_id[rule_id]

    # -- deciding -------------------------------------------------------------------------------
    def normalize(
        self, record: Mapping[str, Any], *, strict: bool = False
    ) -> tuple[dict[str, Value], list[str]]:
        """Coerce a raw record to the schema's types.

        Returns the typed facts and a list of problems. With ``strict=True`` the
        first problem raises :class:`DataError` instead.
        """
        facts: dict[str, Value] = {}
        problems: list[str] = []
        for name, declared in self.schema.items():
            value, problem = coerce(lookup(record, name), declared)
            if problem is not None:
                message = f"field '{name}': {problem}; treated as missing"
                if strict:
                    raise DataError(message)
                problems.append(message)
            facts[name] = value
        return facts, problems

    def decide(
        self, record: Mapping[str, Any], *, trace: bool = False, strict: bool = False
    ) -> Decision:
        """Evaluate ``record`` and return a fully explained :class:`Decision`.

        Args:
            record: raw input; values are coerced to the schema (CSV strings are fine).
            trace: also record the evaluated condition tree of every rule.
            strict: raise :class:`DataError` on uncoercible values instead of
                treating them as missing.
        """
        facts, warnings = self.normalize(record, strict=strict)

        def resolve(path: tuple[str, ...]) -> Value:
            return facts.get(path[0] if len(path) == 1 else ".".join(path))

        for d in self.derived:
            mark = len(warnings)
            facts[d.name] = evaluate(d.expr, resolve, warnings)
            _label(warnings, mark, f"derived '{d.name}'")

        results: list[RuleResult] = []
        for rule in self.rules:
            mark = len(warnings)
            clause = None
            if trace:
                clause = build_clause(rule.expr, rule.when, resolve, warnings)
                outcome = clause.result
            else:
                value = evaluate(rule.expr, resolve, warnings)
                outcome = value if isinstance(value, bool) else None
            _label(warnings, mark, f"rule '{rule.id}'")
            missing = (
                tuple(f for f in rule.inputs if facts.get(f) is None) if outcome is None else ()
            )
            results.append(RuleResult(rule.id, rule.outcome, outcome, clause, missing))

        return self._resolve(results, facts, warnings)

    def decide_many(
        self, records: Iterable[Mapping[str, Any]], **options: bool
    ) -> Iterator[Decision]:
        for record in records:
            yield self.decide(record, **options)

    def _resolve(
        self, results: list[RuleResult], facts: dict[str, Value], warnings: list[str]
    ) -> Decision:
        fired = [r for r in results if r.result is True]
        defaulted = not fired
        if defaulted:
            outcome, decisive = self.default, []
        elif self.strategy == "first_match":
            outcome, decisive = fired[0].outcome, [fired[0]]
        else:
            outcome = max((r.outcome for r in fired), key=self.severity)
            decisive = [r for r in fired if r.outcome == outcome]

        reasons = [self._reason(r.rule_id, facts) for r in decisive]
        if defaulted:
            reasons = ["No rule matched; the default outcome applies."]
        decisive_ids = [r.rule_id for r in decisive]

        escalated = False
        blockers = self._unknown_blockers(results, outcome, defaulted)
        if blockers and self.on_unknown is not None:
            names = sorted({f for r in blockers for f in r.missing})
            ids = ", ".join(r.rule_id for r in blockers)
            message = (
                f"Could not evaluate rule(s) {ids} because {', '.join(names) or 'data'} "
                f"is missing or invalid; escalated to '{self.on_unknown}'."
            )
            outcome, escalated = self.on_unknown, True
            reasons = [message, *(() if defaulted else reasons)]
            decisive_ids = [r.rule_id for r in blockers]

        return Decision(
            outcome=outcome,
            decisive=tuple(decisive_ids),
            reasons=tuple(reasons),
            defaulted=defaulted and not escalated,
            escalated=escalated,
            rule_results=tuple(results),
            facts=facts,
            warnings=tuple(warnings),
            policy=self.ref,
        )

    def _unknown_blockers(
        self, results: list[RuleResult], outcome: str, defaulted: bool
    ) -> list[RuleResult]:
        """Unknown rules that could have changed the outcome had their data been present."""
        if self.on_unknown is None:
            return []
        if self.strategy == "first_match":
            # An unknown rule ordered before the decisive one might have fired first.
            limit = next((i for i, r in enumerate(results) if r.result is True), len(results))
            return [
                r
                for i, r in enumerate(results)
                if i < limit and r.result is None and r.outcome != outcome
            ]
        base = self.severity(outcome)
        # Escalate only if it can raise severity; never downgrade a decision.
        if self.severity(self.on_unknown) <= base:
            return []
        return [r for r in results if r.result is None and self.severity(r.outcome) > base]

    def _reason(self, rule_id: str, facts: Mapping[str, Value]) -> str:
        return render_template(self.rule(rule_id).reason, facts)


def _label(warnings: list[str], mark: int, label: str) -> None:
    for i in range(mark, len(warnings)):
        warnings[i] = f"{label}: {warnings[i]}"


def render_template(template: str, facts: Mapping[str, Value]) -> str:
    """Fill ``{field}`` / ``{field:spec}`` placeholders; ``{{`` and ``}}`` escape braces."""

    def sub(match: re.Match[str]) -> str:
        token = match.group(0)
        if token == "{{":
            return "{"
        if token == "}}":
            return "}"
        value = facts.get(match.group(1))
        spec = match.group(2)
        if value is None:
            return "n/a"
        if spec and _FORMAT_SPEC.match(spec):
            try:
                return format(value, spec)
            except (ValueError, TypeError):
                pass
        return display(value)

    return _PLACEHOLDER.sub(sub, template)


def template_fields(template: str) -> list[tuple[str, str | None]]:
    return [(m.group(1), m.group(2)) for m in _PLACEHOLDER.finditer(template) if m.group(1)]


# -- loading ------------------------------------------------------------------------------------


def _parse_toml(text: str) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise PolicyError(f"invalid TOML: {exc}") from exc


def load_with_diagnostics(path: str | Path) -> tuple[Policy | None, list[Diagnostic]]:
    """Load ``path`` without raising on validation problems.

    Returns the policy (``None`` if it has errors) and every diagnostic found.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyError(f"cannot read policy file {path}: {exc.strerror or exc}") from exc
    try:
        data = _parse_toml(text)
    except PolicyError as exc:
        return None, [Diagnostic(ERROR, "E100", str(exc))]
    return build_policy(data, str(path))


def _require(policy: Policy | None, diagnostics: list[Diagnostic], origin: str) -> Policy:
    if policy is None or has_errors(diagnostics):
        errors = [d for d in diagnostics if d.severity == ERROR]
        lines = [f"{origin}: {len(errors)} error(s)"]
        for d in errors:
            where = f" ({d.location})" if d.location else ""
            lines.append(f"  {d.code} {d.message}{where}")
        raise PolicyError("\n".join(lines), tuple(diagnostics))
    return policy


class _Builder:
    def __init__(self, data: dict[str, Any], origin: str | None) -> None:
        self.data = data
        self.origin = origin
        self.diags: list[Diagnostic] = []

    def err(
        self, code: str, message: str, location: str | None = None, rule: str | None = None
    ) -> None:
        self.diags.append(Diagnostic(ERROR, code, message, rule, location))

    def warn(
        self, code: str, message: str, location: str | None = None, rule: str | None = None
    ) -> None:
        self.diags.append(Diagnostic(WARNING, code, message, rule, location))

    def expr_err(self, exc: ExpressionError, location: str, rule: str | None) -> None:
        self.diags.append(Diagnostic(ERROR, exc.code, exc.message, rule, location, exc.render()))

    def unknown_keys(self, table: dict[str, Any], allowed: set[str], where: str) -> None:
        for key in table:
            if key not in allowed:
                self.err("E101", f"unknown key '{key}' in {where}", where)


def build_policy(
    data: dict[str, Any], origin: str | None = None
) -> tuple[Policy | None, list[Diagnostic]]:
    """Validate ``data`` and assemble a :class:`Policy`; see :class:`_Builder` for diagnostics."""
    b = _Builder(data, origin)
    b.unknown_keys(data, _TOP_KEYS, "top level")

    meta = _meta(b)
    schema = _schema(b)
    if meta is None or schema is None:
        return None, sorted(b.diags, key=Diagnostic.sort_key)
    name, version, description, strategy, outcomes, default, on_unknown = meta

    types = {n: static_type(t) for n, t in schema.items()}
    derived = _derived(b, schema, types)
    rules = _rules(b, outcomes, schema, types, derived)
    tests = _tests(b, outcomes, schema, rules)

    if has_errors(b.diags):
        return None, sorted(b.diags, key=Diagnostic.sort_key)

    policy = Policy(
        name=name,
        version=version,
        description=description,
        strategy=strategy,
        outcomes=outcomes,
        default=default,
        on_unknown=on_unknown,
        schema=schema,
        derived=tuple(derived),
        rules=tuple(rules),
        tests=tuple(tests),
        fingerprint=_fingerprint(
            name, version, strategy, outcomes, default, on_unknown, schema, derived, rules
        ),
        origin=origin,
        _severity={o: i for i, o in enumerate(outcomes)},
        _by_id={r.id: r for r in rules},
    )
    return policy, sorted(b.diags, key=Diagnostic.sort_key)


def _meta(b: _Builder) -> tuple[str, str, str, str, tuple[str, ...], str, str | None] | None:
    section = b.data.get("policy")
    if not isinstance(section, dict):
        b.err("E102", "missing [policy] section", "policy")
        return None
    b.unknown_keys(section, _POLICY_KEYS, "[policy]")

    ok = True
    name = section.get("name")
    if not isinstance(name, str) or not name.strip():
        b.err("E102", "[policy] needs a non-empty string 'name'", "policy.name")
        ok = False
    version = section.get("version", "1")
    if not isinstance(version, str | int | float) or isinstance(version, bool):
        b.err("E102", "[policy] 'version' must be a string or number", "policy.version")
        ok = False
    strategy = section.get("strategy", "first_match")
    if strategy not in STRATEGIES:
        b.err(
            "E102", f"[policy] 'strategy' must be one of {', '.join(STRATEGIES)}", "policy.strategy"
        )
        ok = False
    outcomes = section.get("outcomes")
    if (
        not isinstance(outcomes, list)
        or len(outcomes) < 2
        or not all(isinstance(o, str) and o for o in outcomes)
        or len(set(outcomes)) != len(outcomes)
    ):
        b.err(
            "E102",
            "[policy] 'outcomes' must list at least two distinct names, from least to most severe",
            "policy.outcomes",
        )
        return None
    default = section.get("default", outcomes[0])
    if default not in outcomes:
        b.err(
            "E102", f"[policy] 'default' {default!r} is not one of the outcomes", "policy.default"
        )
        ok = False
    on_unknown = section.get("on_unknown")
    if on_unknown is not None and on_unknown not in outcomes:
        b.err(
            "E102",
            f"[policy] 'on_unknown' {on_unknown!r} is not one of the outcomes",
            "policy.on_unknown",
        )
        ok = False
    description = section.get("description", "")
    if not ok:
        return None
    return (
        str(name),
        str(version),
        str(description),
        str(strategy),
        tuple(outcomes),
        str(default),
        on_unknown,
    )


def _schema(b: _Builder) -> dict[str, str] | None:
    raw = b.data.get("schema")
    if not isinstance(raw, dict) or not raw:
        b.err("E103", "missing or empty [schema] section; declare every field rules use", "schema")
        return None
    schema: dict[str, str] = {}
    for fname, ftype in flatten_schema(raw).items():
        if ftype not in DECLARED_TYPES:
            b.err(
                "E103",
                f"field '{fname}' has invalid type {ftype!r}; use {', '.join(DECLARED_TYPES)}",
                f"schema.{fname}",
            )
        else:
            schema[fname] = ftype
    return schema


def _derived(b: _Builder, schema: dict[str, str], types: dict[str, str]) -> list[Derived]:
    raw = b.data.get("derived", {})
    if not isinstance(raw, dict):
        b.err("E104", '[derived] must be a table of name = "expression"', "derived")
        return []
    out: list[Derived] = []
    known: dict[str, str] = dict(types)
    for dname, source in raw.items():
        loc = f"derived.{dname}"
        if dname in schema:
            b.err("E104", f"derived field '{dname}' collides with a schema field", loc)
            continue
        if not isinstance(source, str):
            b.err("E104", f"derived field '{dname}' must be an expression string", loc)
            continue
        try:
            expr = parse(source)
            dtype = typecheck(expr, source, known.get, known)
        except ExpressionError as exc:
            b.expr_err(exc, loc, None)
            continue
        if dtype == "null":
            b.err("E003", f"derived field '{dname}' has no determinable type", loc)
            continue
        known[dname] = dtype
        out.append(Derived(dname, source, expr, dtype))
    return out


def _rules(
    b: _Builder,
    outcomes: tuple[str, ...],
    schema: dict[str, str],
    types: dict[str, str],
    derived: list[Derived],
) -> list[Rule]:
    raw = b.data.get("rules")
    if not isinstance(raw, list) or not raw:
        b.err("E105", "policy needs at least one [[rules]] entry", "rules")
        return []
    known: dict[str, str] = dict(types)
    deps: dict[str, tuple[str, ...]] = {}
    for d in derived:
        known[d.name] = d.type
        deps[d.name] = _root_inputs(referenced_fields(d.expr), deps, schema)

    rules: list[Rule] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            b.err("E105", f"rules[{i}] must be a table", f"rules[{i}]")
            continue
        b.unknown_keys(entry, _RULE_KEYS, f"rules[{i}]")
        rid = entry.get("id")
        if not isinstance(rid, str) or not _ID.match(rid):
            b.err(
                "E105",
                f"rules[{i}] needs an 'id' (letters, digits, _ . -; starting with a letter)",
                f"rules[{i}].id",
            )
            continue
        loc = f"rules[{rid}]"
        if rid in seen:
            b.err("E106", f"duplicate rule id '{rid}'", loc, rid)
            continue
        seen.add(rid)
        outcome = entry.get("outcome")
        if outcome not in outcomes:
            b.err(
                "E107",
                f"outcome {outcome!r} is not declared in [policy] outcomes",
                f"{loc}.outcome",
                rid,
            )
            continue
        when = entry.get("when")
        if not isinstance(when, str):
            b.err("E105", f"rule '{rid}' needs a 'when' expression string", f"{loc}.when", rid)
            continue
        try:
            expr = parse(when)
            rtype = typecheck(expr, when, known.get, known)
        except ExpressionError as exc:
            b.expr_err(exc, f"{loc}.when", rid)
            continue
        if rtype != "bool":
            b.err(
                "E108",
                f"'when' must be a boolean condition, but this is {rtype}",
                f"{loc}.when",
                rid,
            )
            continue
        description = str(entry.get("description", ""))
        reason = entry.get("reason")
        if reason is None:
            b.warn(
                "W108",
                "rule has no 'reason'; decisions will show a generic message",
                f"{loc}.reason",
                rid,
            )
            reason = description or f"Rule {rid} matched"
        elif not isinstance(reason, str):
            b.err("E105", f"rule '{rid}': 'reason' must be a string", f"{loc}.reason", rid)
            continue
        else:
            _check_reason(b, reason, known, loc, rid)
        tags = entry.get("tags", [])
        rules.append(
            Rule(
                id=rid,
                when=when,
                expr=expr,
                outcome=outcome,
                reason=reason,
                description=description,
                tags=tuple(str(t) for t in tags) if isinstance(tags, list) else (),
                inputs=_root_inputs(referenced_fields(expr), deps, schema),
            )
        )
    return rules


def _root_inputs(
    names: Iterable[str], deps: dict[str, tuple[str, ...]], schema: dict[str, str]
) -> tuple[str, ...]:
    """Expand derived-field references into the schema fields they ultimately read."""
    found: dict[str, None] = {}
    for name in names:
        if name in deps:
            found.update(dict.fromkeys(deps[name]))
        elif name in schema:
            found.setdefault(name, None)
    return tuple(found)


def _check_reason(b: _Builder, reason: str, known: dict[str, str], loc: str, rid: str) -> None:
    for fname, spec in template_fields(reason):
        if fname not in known:
            b.warn("W105", f"reason refers to unknown field '{fname}'", f"{loc}.reason", rid)
        if spec and not _FORMAT_SPEC.match(spec):
            b.warn("W105", f"reason has an unsupported format spec {spec!r}", f"{loc}.reason", rid)


def _tests(
    b: _Builder, outcomes: tuple[str, ...], schema: dict[str, str], rules: list[Rule]
) -> list[PolicyTest]:
    raw = b.data.get("tests", [])
    if not isinstance(raw, list):
        b.err("E109", "[[tests]] must be an array of tables", "tests")
        return []
    rule_ids = {r.id for r in rules}
    tests: list[PolicyTest] = []
    for i, entry in enumerate(raw):
        loc = f"tests[{i}]"
        if not isinstance(entry, dict):
            b.err("E109", f"{loc} must be a table", loc)
            continue
        b.unknown_keys(entry, _TEST_KEYS, loc)
        tname = entry.get("name", f"case {i + 1}")
        data = entry.get("input")
        expect = entry.get("expect")
        if not isinstance(data, dict) or expect not in outcomes:
            b.err(
                "E109",
                f"{loc} ({tname}) needs an 'input' table and an 'expect' outcome from the policy",
                loc,
            )
            continue
        unknown = sorted(set(flatten_schema(data)) - set(schema))
        if unknown:
            b.err(
                "E110", f"{loc} ({tname}) uses fields not in the schema: {', '.join(unknown)}", loc
            )
            continue
        expect_rules = entry.get("expect_rules")
        if expect_rules is not None:
            if not isinstance(expect_rules, list) or not set(expect_rules) <= rule_ids:
                b.err("E109", f"{loc} ({tname}): 'expect_rules' must list existing rule ids", loc)
                continue
            expect_rules = tuple(expect_rules)
        tests.append(PolicyTest(str(tname), data, expect, expect_rules))
    return tests


def _fingerprint(
    name: str,
    version: str,
    strategy: str,
    outcomes: tuple[str, ...],
    default: str,
    on_unknown: str | None,
    schema: dict[str, str],
    derived: list[Derived],
    rules: list[Rule],
) -> str:
    """Hash of everything that can change a decision (comments and tests excluded)."""
    canonical = {
        "name": name,
        "version": version,
        "strategy": strategy,
        "outcomes": list(outcomes),
        "default": default,
        "on_unknown": on_unknown,
        "schema": dict(sorted(schema.items())),
        "derived": [[d.name, to_source(d.expr)] for d in derived],
        "rules": [[r.id, to_source(r.expr), r.outcome, r.reason] for r in rules],
    }
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


__all__ = [
    "STRATEGIES",
    "Derived",
    "Policy",
    "PolicyTest",
    "Rule",
    "build_policy",
    "load_with_diagnostics",
    "render_template",
]
