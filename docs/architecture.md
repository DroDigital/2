# Architecture and design decisions

RuleLens is about 4,000 lines of typed Python (plus about 2,300 lines of tests) with no runtime dependencies. This page explains how it
fits together and why it is built the way it is.

## Pipeline

```
  policy.toml                                       records (CSV / JSON / dict)
      │                                                       │
      ▼                                                       ▼
  tomllib ──► policy.build_policy ──► Policy ◄──── schema.coerce (typed facts)
                   │                    │
        lexer ► parser ► typecheck      │ decide()
        (every `when`, `[derived]`)     ▼
                                    evaluator (three-valued) ──► Decision
                                        │                         outcome · reasons
                                        └─► trace.build_clause    fingerprint · trace
                                            (why-trees)
  Policy ──► analysis.lint ──► unsatisfiable / shadowed / redundant rules
  Policy × data ──► simulate ──► rule stats · data gaps · group audit
  Policy × Policy × data ──► diff ──► transitions · drivers · gate ──► text / JSON / HTML
```

| Module | Responsibility |
|---|---|
| `lexer.py`, `parser.py`, `nodes.py` | tokens, Pratt parser, immutable AST with source spans, canonical printer |
| `typecheck.py`, `functions.py` | static types and the whitelisted function signatures |
| `evaluator.py` | three-valued evaluation; never raises on bad data |
| `schema.py` | declared field types and coercion of raw input |
| `policy.py` | TOML loading, exhaustive validation, fingerprint, `decide()` and the strategies |
| `decision.py`, `trace.py` | result model, explanations, per-clause evaluation trees |
| `analysis.py` | static analysis (interval and value-set abstraction) |
| `simulate.py`, `diff.py` | batch replay, group audit, policy impact diff |
| `report.py`, `html_report.py`, `cli.py` | text/JSON/HTML output and the command line |

## Key decisions

**A hand-written parser, never `eval`.** Rules are authored by analysts and arrive in pull
requests; running them through `eval` would make every policy file an arbitrary-code risk. A
small Pratt parser also gives precise error spans, a canonical printer (used for fingerprints and
a round-trip property test), and a closed set of operations that static analysis can reason
about. Resource limits (length, nesting depth, no loops or recursion) bound evaluation cost.

**Static typing.** A misspelled field in a rule that silently evaluates to "unknown" is exactly
the kind of bug that goes unnoticed for months. Type errors surface at load time, with carets.

**Three-valued logic.** Real data has gaps. Treating "missing" as `false` makes `not (x > 5)` true
for missing `x`, which is rarely intended. Kleene logic gives well-defined semantics, lets
`false and unknown` resolve to `false`, and lets the engine say *which* rules could not run and
why. `on_unknown` turns that into an explicit, auditable fail-safe policy.

**`most_severe` as the default shape for risk rules.** Under first-match, appending a rule can
change or shadow earlier behaviour in non-obvious ways. Under `most_severe`, outcomes are ordered,
every rule is evaluated, and adding a rule can only raise severity, never quietly weaken a
decline. `first_match` stays available for genuine decision tables, where order is the logic, and
the linter checks order mistakes.

**Every rule is always evaluated.** It costs a little speed, but explanations, "why not"
information, coverage statistics and dead-rule detection all need the full picture.

**TOML.** Comments, readable diffs in code review, and a parser in the standard library (3.11+).
Policies are meant to be reviewed like code.

**Fingerprints identify behaviour.** Audit logs need "which version made this decision". A
version string is only as honest as whoever bumped it; a hash of the normalised rules cannot
drift.

**Group audit uses columns the policy never reads.** The right way to look for disparate impact
is to compare outcomes across attributes the rules do not use. `--group-by` therefore reads a
raw column that need not be in the schema. The four-fifths ratio is a screening heuristic and is
labelled as one everywhere it appears.

## Static analysis

Each rule condition is converted to negation normal form and then to a union of *boxes* (a
disjunction of conjunctions). A box constrains fields to an interval set (numbers) or a value set
(strings, booleans). Negation is pushed to the atoms by complementing the atom's domain.

From boxes we get three checks:

* **unsatisfiable**: no box survives intersection (`age > 65 and age < 18`).
* **implication** `A ⟹ B`: every box of A fits inside some box of B. Used for *shadowed* rules
  (first-match: an earlier rule B with A ⟹ B) and *redundant* rules (most-severe: A ⟹ B where B's
  outcome is strictly more severe).

**Soundness argument.**

1. *Negation is exact.* Kleene logic is a De Morgan algebra, so `not (p and q) = not p or not q`
   holds with unknowns; pushing `not` inwards loses nothing. An atom is true only for a non-null
   value, and its complement is taken within non-null values, which is what `not (x < 5)` means.
2. *Opaque conditions only widen.* Anything the abstraction cannot express becomes an "opaque"
   flag on a box. For the left side of an implication that over-approximates it (safe). A box
   from the right side that is opaque never counts as a cover. Unsatisfiability of the modelled
   atoms still proves the whole conjunction empty.
3. *Presence is required explicitly.* For `A ⟹ B`, every field B constrains must also be
   constrained in A. Otherwise A could fire on a record where that field is null while B is
   unknown. The property tests found exactly this bug in a deliberately weakened version.
4. *Real intervals over-approximate integers.* Emptiness and containment over the reals imply
   the same over the integers, so the analysis may miss a finding but never fabricates one.

`tests/test_analysis_properties.py` checks the two headline claims against the evaluator. It
generates random conditions (comparisons, membership, booleans, opaque atoms, `and`/`or`/`not`)
and enumerates a grid of 1,500 records covering every distinguishable region of the literals,
including missing values for every field. During development the tests were also run against
deliberately broken variants of the analyzer and the printer, and failed as they should.

Complexity guard: DNF expansion is capped at 64 boxes; larger conditions are skipped silently
(no finding) rather than risking exponential blow-up.

## Impact diff

`rulelens diff` evaluates each record under both policies, each normalising the raw record against
its own schema, so the policies may declare different fields. It reports the outcome transition
matrix, which deciding rules moved records, records that kept their outcome for different reasons
(relevant for adverse-action notices), examples with both explanations, fields a policy reads
that the data lacks, and optional group audits for both versions. `--max-change-rate` exits with
status 3 above a threshold, turning "how much does this change affect?" into a CI check.

## Testing strategy

| Layer | Technique |
|---|---|
| lexer, parser | table tests for errors; **round-trip property**: `parse(print(tree)) == tree` over random ASTs |
| evaluator | exhaustive Kleene truth tables; **differential test** against Python's own semantics on generated expressions |
| policy | one test per diagnostic code; fingerprint stability; strategy and `on_unknown` semantics; traced and untraced evaluation must agree (property) |
| analysis | unit tests for domains and implication; **soundness properties** against the evaluator; a non-vacuity test proving the properties exercise real implications |
| CLI and reports | exit codes, JSON validity, HTML escaping of hostile input, light/dark theme markers |
| examples | every shipped policy must lint clean and pass its own embedded tests; datasets must be reproducible from the seeded generator |

CI runs the suite on Python 3.11-3.13 with `ruff`, `mypy --strict` and a coverage floor, then
builds and installs the wheel.

## Known limitations

* Pure Python tree-walking evaluator: tens of thousands of decisions per second per core, not
  millions. See the benchmark in the README.
* Numbers are floats. For exact money arithmetic, supply integer cents.
* The analysis does not reason about arithmetic across fields, dates, function calls or
  integer-ness (it is sound, not complete).
* No persistence, UI, authoring workflow or server. It is a library and CLI meant to sit inside
  your own review and deployment process.
* It evaluates rules; it does not tell you whether a rule is lawful or fair. The group audit
  finds disparities worth investigating and nothing more.
