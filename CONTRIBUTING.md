# Contributing

```bash
pip install -e ".[dev]"
make check        # ruff, ruff format --check, mypy --strict, pytest (coverage floor 90%)
```

## Ground rules

* **Behaviour changes need tests.** Parser or evaluator changes should keep the property tests
  green; analysis changes must keep `tests/test_analysis_properties.py` green, since that file is
  what justifies the "sound" claim. If you add a check, add a property that tries to falsify it.
* **Every new diagnostic gets a code** in `docs/language.md` and a test in `tests/test_policy.py`
  or `tests/test_analysis.py`.
* **Examples are code.** `tests/test_examples.py` requires shipped policies to lint clean and pass
  their embedded tests. If you change `examples/generate_data.py`, regenerate the CSVs with
  `make data` and commit them; a test checks they match.
* **No runtime dependencies.** Dev tooling is fine; the library itself stays standard-library only.
* **No `eval`/`exec`** anywhere in `src/`.
* Keep claims in the docs measurable: if you quote a number, show how to reproduce it.

## Reporting problems

Please include the policy (or a reduced version), a record that triggers it, and the output of
`rulelens --version`.
