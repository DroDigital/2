"""Throughput benchmark: ``python benchmarks/bench.py``.

Measures decisions per second on the bundled synthetic datasets. Numbers depend heavily on the
machine; compare runs on the same machine, not across machines.
"""

from __future__ import annotations

import platform
import statistics
import sys
import time
from collections.abc import Callable
from pathlib import Path

from rulelens import Policy
from rulelens.analysis import lint
from rulelens.dataio import read_records

ROOT = Path(__file__).resolve().parent.parent / "examples"
CASES = [
    ("lending", "applications.csv"),
    ("fraud", "transactions.csv"),
    ("claims", "claims.csv"),
    ("trials", "screening.csv"),
]
REPEATS = 7


def best_of(fn: Callable[[], object], repeats: int = REPEATS) -> float:
    """Median wall-clock seconds over ``repeats`` runs, after one warm-up."""
    fn()
    times = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        times.append(time.perf_counter() - start)
    return statistics.median(times)


def main() -> None:
    print(f"Python {platform.python_version()} on {platform.machine()} ({platform.system()})")
    print(
        f"{'policy':<10} {'rules':>5} {'records':>8} {'decide/s':>10} {'traced/s':>10} {'lint ms':>8}"
    )
    for folder, dataset in CASES:
        policy = Policy.load(ROOT / folder / "policy.toml")
        records = list(read_records(ROOT / folder / dataset))
        plain = best_of(lambda p=policy, r=records: [p.decide(x) for x in r])  # type: ignore[misc]
        traced = best_of(lambda p=policy, r=records: [p.decide(x, trace=True) for x in r])  # type: ignore[misc]
        lint_s = best_of(lambda p=policy: lint(p))  # type: ignore[misc]
        n = len(records)
        print(
            f"{folder:<10} {len(policy.rules):>5} {n:>8,} {n / plain:>10,.0f} "
            f"{n / traced:>10,.0f} {lint_s * 1000:>8.1f}"
        )
    sys.stdout.flush()


if __name__ == "__main__":
    main()
