# Policy language reference

A policy is one TOML file. This page documents every section, the expression language used in
`when` conditions and derived fields, and every diagnostic code `rulelens lint` can emit.

- [Policy file](#policy-file)
- [Expressions](#expressions)
- [Missing data (three-valued logic)](#missing-data-three-valued-logic)
- [Decision strategies](#decision-strategies)
- [Reason templates](#reason-templates)
- [Embedded tests](#embedded-tests)
- [Diagnostic codes](#diagnostic-codes)

## Policy file

```toml
[policy]
name = "mortgage-prequalification"   # required
version = "1.0"                      # optional, default "1"; shown in every decision
description = "..."                  # optional
strategy = "most_severe"             # "first_match" (default) or "most_severe"
outcomes = ["approve", "refer", "decline"]   # required, least to most severe, >= 2
default = "approve"                  # applied when no rule fires; default: first outcome
on_unknown = "refer"                 # optional fail-safe, see below

[schema]                             # required: every input field and its type
credit_score = "int"                 # int | number | string | bool | date
applicant.income = "number"          # dotted names read nested JSON ({"applicant": {...}})

[derived]                            # optional: computed fields, evaluated in order
dti = "monthly_debt * 12 / annual_income"

[[rules]]
id = "DTI_HIGH"                      # unique; letters, digits, _ . -
when = "dti > 0.43"                  # boolean expression
outcome = "refer"                    # must be one of `outcomes`
reason = "Debt-to-income {dti:.1%} is above the 43% guideline"   # optional but recommended
description = "free text"            # optional; not part of the policy fingerprint
tags = ["credit"]                    # optional

[[tests]]                            # optional regression cases
name = "..."
input = { credit_score = 540 }
expect = "decline"
expect_rules = ["CREDIT_SCORE_LOW"]  # optional: exact set of decisive rules
```

Unknown keys are errors, so a typo like `outcom = ...` cannot silently disable something.

**Schema types.** Input is coerced to the declared type, so CSV text works directly. `int` and
`number` accept numeric strings (non-finite values are rejected); `bool` accepts
`true/false/yes/no/y/n/t/f/1/0`; `date` accepts `YYYY-MM-DD` (a trailing time is ignored); empty
strings are *missing*. A value that cannot be coerced becomes missing and is reported as a
warning on the decision (or raises with `strict=True` / `--strict`).

**Derived fields** may reference schema fields and earlier derived fields only.

**Fingerprint.** Every decision records `name`, `version` and a 12-character hash of everything
that can change a decision: strategy, outcomes, default, `on_unknown`, schema, derived fields and
each rule's id, normalised condition, outcome and reason. Whitespace, comments, descriptions, tags
and tests do not change it, so the fingerprint identifies *behaviour*.

## Expressions

```
income >= 50000 and (dti < 0.4 or credit_score in [720, 740, 760]) and region != "X"
days_between(policy_start, loss_date) < 30
coalesce(override_score, bureau_score, 0) >= 650
```

### Literals

| Kind | Examples |
|---|---|
| number | `42`, `3.14`, `1e6`, `-5` |
| string | `'single'`, `"double"`, escapes `\\ \' \" \n \t` |
| boolean | `true`, `false` |
| null | `null` (only with `is null`; see below) |
| list | `[1, 2, 3]`, `['a', 'b']` (only as the right side of `in`; one element type) |

### Operators, highest precedence first

| Operators | Notes |
|---|---|
| `f(x)`, `a.b` | calls and dotted field paths |
| unary `-` | numbers |
| `*` `/` `%` | numbers; `/` or `%` by zero is *unknown* with a warning |
| `+` `-` | numbers |
| `==` `!=` `<` `<=` `>` `>=` `in` `not in` `is null` `is not null` | **do not chain**: `a < b < c` is an error; write `a < b and b < c` |
| `not` | |
| `and` | |
| `or` | |

### Types

Expressions are type-checked when the policy loads, before any data is seen. `income < "high"`,
a misspelled field, a wrong argument count, or a `when` that is not boolean is an error with a
caret pointing at the problem, and typo suggestions:

```
error E002 rules[LOW_SCORE].when: unknown field 'credit_scor'; did you mean 'credit_score'?
    credit_scor < 580
    ^^^^^^^^^^^
```

`==` / `!=` require the same type on both sides. Ordering works on numbers, strings and dates.
Writing `x == null` is rejected, because it would always be unknown; use `x is null`.

### Functions

All functions are pure and deterministic. A `null` argument yields `null`, except `coalesce`.

| Function | Result |
|---|---|
| `abs(n)` | absolute value |
| `round(n)`, `round(n, digits)` | rounds half to even (Python semantics) |
| `min(a, b, ...)`, `max(a, b, ...)` | numbers |
| `len(s)` | string length |
| `lower(s)`, `upper(s)` | |
| `contains(s, sub)`, `startswith(s, p)`, `endswith(s, p)` | substring tests |
| `between(x, lo, hi)` | inclusive; numbers, strings or dates, all the same type |
| `days_between(a, b)` | whole days from date `a` to date `b` (negative if `b` is earlier) |
| `date('YYYY-MM-DD')` | a date literal |
| `coalesce(a, b, ...)` | first non-null argument |

There is deliberately no regular-expression function, loops, assignment, or user-defined
functions: policies stay analysable and cannot be made to run for a long time. Expressions are
limited to 2,000 characters and 48 levels of nesting.

## Missing data (three-valued logic)

A missing field is *unknown*, not false. Unknown propagates like SQL `NULL`:

| `a` | `b` | `a and b` | `a or b` |
|---|---|---|---|
| true | unknown | unknown | **true** |
| false | unknown | **false** | unknown |
| unknown | unknown | unknown | unknown |

`not unknown` is unknown. Any comparison or arithmetic involving unknown is unknown. A rule
fires only when its condition is *definitely true*; unknown rules are reported separately
(`Decision.unknown`, `Decision.missing_fields`).

This is why `prior_cardiac_event and months_since_cardiac_event < 6` works when the month count is
blank for people with no event: `false and unknown` is `false`.

### `on_unknown`: fail safe instead of failing open

Without `on_unknown`, a rule that cannot be evaluated simply does not fire, so a missing credit
score could quietly lead to an approval. With `on_unknown = "refer"`:

* `most_severe`: if an unknown rule has a *more severe* outcome than the decision, and
  `on_unknown` is more severe than the decision, the outcome becomes `on_unknown`. A decision is
  never downgraded, and a known decline is never softened by missing data.
* `first_match`: if an unknown rule *ahead of* the decisive rule has a different outcome, it
  might have matched first, so the outcome becomes `on_unknown`.

The decision is flagged `escalated`, and its reason names the rules that could not be evaluated
and the missing fields.

## Decision strategies

| Strategy | Meaning | Use when |
|---|---|---|
| `first_match` | rules are checked in file order; the first that fires decides | decision tables, triage, routing |
| `most_severe` | all rules are checked; the most severe outcome among those that fired wins | screening, risk, eligibility: adding a rule can never weaken a decline |

In both, **every** rule is evaluated so explanations and statistics are complete. `decisive`
lists the rules that explain the outcome: one rule for `first_match`, all rules with the winning
outcome for `most_severe` (so an adverse-action notice can list every reason). If nothing fires,
the `default` outcome applies and `Decision.defaulted` is true.

## Reason templates

`reason = "Score {credit_score} is below {minimum}"`. `{field}` inserts a schema or derived
value; `{field:spec}` applies a Python format spec (`{dti:.1%}`, `{amount:,.0f}`,
`{opened:%Y-%m-%d}`). Missing values render as `n/a`. `{{` and `}}` produce literal braces.
Specs are restricted to a short safe character set.

## Embedded tests

`[[tests]]` are regression cases that live next to the rules. `rulelens test` fails (exit 1) if
a case returns a different outcome, or, when `expect_rules` is given, a different set of decisive
rules. Inputs may only use schema fields, so a typo in a test is an error rather than a vacuous
pass.

## Diagnostic codes

Errors make a policy unusable. Warnings are findings about the rules; `lint --strict` fails on
them. Info items are housekeeping.

| Code | Severity | Meaning |
|---|---|---|
| E001 | error | expression syntax error |
| E002 | error | unknown field or function (with a "did you mean" suggestion) |
| E003 | error | type error in an expression |
| E100 | error | file is not valid TOML |
| E101 | error | unknown key |
| E102 | error | problem in the `[policy]` section |
| E103 | error | missing, empty or invalid `[schema]` |
| E104 | error | invalid `[derived]` field |
| E105 | error | malformed rule |
| E106 | error | duplicate rule id |
| E107 | error | rule outcome not declared in `outcomes` |
| E108 | error | `when` is not a boolean condition |
| E109 | error | malformed `[[tests]]` entry |
| E110 | error | a test uses a field that is not in the schema |
| W101 | warning | rule can never fire (its conditions contradict each other) |
| W102 | warning | rule is shadowed by an earlier rule (`first_match`) |
| W103 | warning | rule is redundant: another rule with a more severe outcome always fires with it (`most_severe`) |
| W105 | warning | a reason template refers to an unknown field or uses an unsupported format spec |
| W108 | warning | rule has no `reason` |
| I201 | info | schema field not used by any rule |
| I202 | info | derived field not used by any rule |
| I203 | info | policy has no embedded tests |

### What the static analysis can and cannot prove

W101-W103 are **sound but incomplete**: a reported finding is always true, but some real problems
are not reported. Conditions are analysed when they are combinations of comparisons of a field
(or derived field) against a literal, `in` lists, boolean fields, and `and` / `or` / `not`.
Anything else (arithmetic between fields, function calls, `is null`, dates, comparisons between two
fields) is treated as an unknown extra condition, which can hide a finding but never invent one.
Integer-ness is not considered, so `x > 5 and x < 6` on an `int` field is not reported as empty.
See [architecture.md](architecture.md#static-analysis) for the argument.
