"""Generate the synthetic datasets used by the examples (deterministic, standard library only).

    python examples/generate_data.py

Every file is SYNTHETIC. Distributions are invented to give the example policies something
interesting to do; they do not describe any real population. The lending data deliberately gives
one region lower average income and credit scores, so the outcome-rate audit has something to
find. That is a property of this made-up data, not a claim about any real lender or region.
"""

from __future__ import annotations

import csv
import math
import random
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent


def clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def write(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows):>5} rows to {path.relative_to(HERE.parent)}")


def blank(rng: random.Random, value: object, p: float) -> object:
    """Return an empty cell with probability ``p`` to simulate missing data."""
    return "" if rng.random() < p else value


def lending(rng: random.Random, n: int = 1200) -> list[dict[str, object]]:
    # region: (share, mean credit score, median income)
    regions = {
        "North": (0.30, 702, 84_000),
        "East": (0.25, 694, 79_000),
        "West": (0.20, 698, 86_000),
        "South": (0.25, 668, 66_000),
    }
    names = list(regions)
    weights = [regions[r][0] for r in names]
    rows = []
    for i in range(1, n + 1):
        region = rng.choices(names, weights)[0]
        _, mean_score, median_income = regions[region]
        score = int(clip(rng.gauss(mean_score, 62), 420, 850))
        income = round(rng.lognormvariate(math.log(median_income), 0.33) / 500) * 500
        dti = clip(rng.betavariate(2.0, 5.5) * 0.85, 0.02, 0.8)
        property_value = int(round(rng.lognormvariate(math.log(330_000), 0.32), -3))
        ltv = clip(rng.betavariate(5.5, 2.4), 0.35, 1.02)
        rows.append(
            {
                "applicant_id": f"APP-{i:05d}",
                "region": region,
                "credit_score": blank(rng, score, 0.03),
                "annual_income": blank(rng, income, 0.01),
                "monthly_debt": round(dti * income / 12),
                "loan_amount": int(round(ltv * property_value, -3)),
                "property_value": property_value,
                "employment_months": 1 + int(rng.expovariate(1 / 84)),
                "bankruptcies_7y": rng.choices([0, 1, 2], [0.965, 0.03, 0.005])[0],
            }
        )
    return rows


def fraud(rng: random.Random, n: int = 1500) -> list[dict[str, object]]:
    countries = ["US", "GB", "DE", "CA", "FR", "AU"]
    domains = ["gmail.com", "outlook.com", "yahoo.com", "icloud.com", "example.com"]
    disposable = ["mailinator.com", "tempmail.io", "guerrillamail.com"]
    rows = []
    for i in range(1, n + 1):
        billing = rng.choices(countries, [50, 12, 10, 12, 8, 8])[0]
        age = int(rng.expovariate(1 / 420))
        avg = round(rng.lognormvariate(math.log(70), 0.6), 2)
        row: dict[str, object] = {
            "txn_id": f"TXN-{i:06d}",
            "amount": round(clip(rng.lognormvariate(math.log(58), 0.9), 1.5, 4000), 2),
            "account_age_days": age,
            "txns_last_hour": rng.choices([0, 1, 2, 3, 4], [30, 40, 18, 8, 4])[0],
            "failed_attempts_24h": rng.choices([0, 1, 2], [90, 8, 2])[0],
            "avg_amount_90d": blank(rng, avg, 0.5 if age < 30 else 0.02),
            "billing_country": billing,
            "ip_country": billing if rng.random() < 0.93 else rng.choice(countries),
            "card_present": rng.random() < 0.08,
            "device_seen_before": rng.random() < 0.82,
            "email_domain": rng.choices(domains + disposable, [30, 20, 20, 15, 10, 1, 1, 1])[0],
        }
        roll = rng.random()  # inject a few recognisable abuse patterns
        if roll < 0.012:
            row.update(txns_last_hour=rng.randint(8, 16))
        elif roll < 0.024:
            row.update(
                failed_attempts_24h=rng.randint(5, 9), amount=round(rng.uniform(0.5, 4.5), 2)
            )
        elif roll < 0.040:
            row.update(
                account_age_days=rng.randint(0, 5),
                amount=round(rng.uniform(550, 2600), 2),
                device_seen_before=False,
            )
        elif roll < 0.055:
            row.update(
                ip_country=rng.choice([c for c in countries if c != billing]),
                device_seen_before=False,
                amount=round(rng.uniform(250, 3500), 2),
            )
        elif roll < 0.062:
            row.update(email_domain=rng.choice(disposable), amount=round(rng.uniform(210, 900), 2))
        rows.append(row)
    return rows


def claims(rng: random.Random, n: int = 800) -> list[dict[str, object]]:
    kinds = {
        "auto": (45, 2_800, 25_000),
        "home": (25, 7_500, 300_000),
        "theft": (12, 2_400, 15_000),
        "medical": (18, 1_100, 50_000),
    }
    names = list(kinds)
    rows = []
    for i in range(1, n + 1):
        kind = rng.choices(names, [kinds[k][0] for k in names])[0]
        _, median, limit = kinds[kind]
        start = date(2021, 1, 1) + timedelta(days=rng.randint(0, 1600))
        early = rng.random() < 0.06
        loss = start + timedelta(days=rng.randint(0, 25) if early else rng.randint(30, 900))
        if rng.random() < 0.01:
            loss = start - timedelta(days=rng.randint(1, 20))  # data-entry problem
        delay = rng.choices(
            [rng.randint(0, 7), rng.randint(8, 60), rng.randint(61, 200)], [80, 14, 6]
        )[0]
        police_p = {"theft": 0.75, "auto": 0.4}.get(kind, 0.1)
        rows.append(
            {
                "claim_id": f"CLM-{i:05d}",
                "claim_amount": round(rng.lognormvariate(math.log(median), 0.8)),
                "coverage_limit": limit,
                "claim_type": kind,
                "policy_active": rng.random() < 0.97,
                "policy_start": "garbled" if rng.random() < 0.01 else start.isoformat(),
                "loss_date": loss.isoformat(),
                "reported_date": (loss + timedelta(days=delay)).isoformat(),
                "prior_claims_12m": rng.choices([0, 1, 2, 3, 4], [72, 18, 6, 3, 1])[0],
                "police_report": blank(rng, rng.random() < police_p, 0.02),
            }
        )
    return rows


def trials(rng: random.Random, n: int = 600) -> list[dict[str, object]]:
    rows = []
    for i in range(1, n + 1):
        event = rng.random() < 0.07
        rows.append(
            {
                "participant_id": f"P-{i:04d}",
                "site": rng.choice(["Boston", "Leeds", "Lyon", "Osaka"]),
                "age": int(clip(rng.gauss(56, 13), 17, 84)),
                "hba1c": blank(rng, round(clip(rng.gauss(8.4, 1.4), 5.2, 13.0), 1), 0.04),
                "egfr": blank(rng, round(clip(rng.gauss(78, 20), 15, 125)), 0.04),
                "bmi": blank(rng, round(clip(rng.gauss(31, 6), 15, 58), 1), 0.02),
                "systolic_bp": blank(rng, round(clip(rng.gauss(134, 16), 90, 210)), 0.02),
                "pregnant": rng.random() < 0.012,
                "on_insulin": rng.random() < 0.18,
                "prior_cardiac_event": event,
                "months_since_cardiac_event": rng.randint(1, 60) if event else "",
            }
        )
    return rows


def main() -> None:
    rng = random.Random(20240601)
    write(HERE / "lending" / "applications.csv", lending(rng))
    write(HERE / "fraud" / "transactions.csv", fraud(rng))
    write(HERE / "claims" / "claims.csv", claims(rng))
    write(HERE / "trials" / "screening.csv", trials(rng))


if __name__ == "__main__":
    main()
