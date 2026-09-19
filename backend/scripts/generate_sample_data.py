"""Generate a realistic — and deliberately messy — retail sales dataset for demos.

The data contains real signal (seasonality, growth, channel shift, regional and
category differences) and real-world mess (currency strings, percentages, mixed
date formats, inconsistent capitalisation, placeholders, duplicates, blank rows)
so both the cleaning pipeline and the analyst have something to show.

It also carries a repeating customer base with a genuine decay curve, so the cohort
view has a real retention story rather than a flat line, and one synthetic email
column on the reserved `example.com` domain, so the privacy guard has something to
find. No value in this file belongs to a real person.

Usage:  python scripts/generate_sample_data.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

# Two and a half years, so a yearly seasonal pattern can be *learned from* one cycle and
# *tested on* the next — two years exactly would leave a backtest nothing to hold out.
ROWS = 4800
DATE_RANGE = ("2022-07-01", "2024-12-31")
SEED = 42
OUTPUT = Path(__file__).resolve().parents[2] / "sample_data" / "retail_sales.csv"

CATALOG = {
    "Electronics": ([("Laptop Pro 14", 1299), ("Wireless Earbuds", 129), ("4K Monitor", 379), ("Smart Watch", 249)],
                    0.78, 0.05, (1, 4)),
    "Furniture": ([("Ergo Office Chair", 289), ("Standing Desk", 549), ("Bookshelf", 159)], 0.55, 0.09, (1, 3)),
    "Office Supplies": ([("Premium Notebook Pack", 24), ("Gel Pen Set", 12), ("Desk Organizer", 35)],
                        0.45, 0.02, (2, 20)),
    "Home & Kitchen": ([("Espresso Machine", 449), ("Air Fryer", 139), ("Chef Knife Set", 89)], 0.60, 0.03, (1, 4)),
}
CATEGORY_WEIGHTS = [0.30, 0.18, 0.27, 0.25]
REGIONS = ["North", "South", "East", "West"]
REGION_WEIGHTS = [0.22, 0.19, 0.24, 0.35]
SEGMENTS = ["Consumer", "Small Business", "Enterprise"]

# Placeholder names over a domain reserved for documentation (RFC 2606). Nothing here
# can reach a real inbox, and nothing here belongs to a real person.
FIRST_NAMES = ["Avery", "Blair", "Casey", "Devon", "Ellis", "Frankie", "Harper", "Indigo",
               "Jules", "Kai", "Linden", "Marlow", "Noor", "Oakley", "Peyton", "Quinn",
               "Rowan", "Sasha", "Tatum", "Wren"]
LAST_NAMES = ["Adeyemi", "Bianchi", "Castillo", "Dubois", "Eriksen", "Fontaine", "Gallagher",
              "Haddad", "Iversen", "Jensen", "Kowalski", "Lindqvist", "Moreau", "Nakamura",
              "Okafor", "Pereira", "Rossi", "Sandoval", "Takahashi", "Vasquez"]
# Chance an order comes from a customer who has not been seen before. The rest of the
# time it is a repeat, which is what gives the retention curve something to measure.
NEW_CUSTOMER_RATE = 0.34
# Probability a customer stops after any given month: a geometric lifetime, so cohorts
# decay steeply at first and flatten into a loyal tail — the shape real retention has.
CHURN_RATE = 0.30


def generate() -> pd.DataFrame:
    rng = np.random.default_rng(SEED)
    days = pd.date_range(*DATE_RANGE, freq="D")
    progress = np.arange(len(days)) / len(days)
    month = days.month.to_numpy()
    seasonality = 1 + 0.45 * np.isin(month, [11, 12]) + 0.12 * np.isin(month, [6, 7]) - 0.15 * np.isin(month, [1, 2])
    # Growth is spread over a longer span than it used to be, so the per-month rate has
    # to be steeper for a year-on-year comparison to still show a real trend.
    weights = seasonality * (1 + 0.50 * progress)
    idx = rng.choice(len(days), size=ROWS, p=weights / weights.sum())
    idx.sort()
    customers = _assign_customers(days[idx], rng)

    records = []
    for n, day_index in enumerate(idx):
        date = days[day_index]
        t = progress[day_index]
        category = rng.choice(list(CATALOG), p=CATEGORY_WEIGHTS)
        products, cost_ratio, return_rate, (lo, hi) = CATALOG[category]
        product, base_price = products[rng.integers(len(products))]
        region = rng.choice(REGIONS, p=REGION_WEIGHTS)
        online_share = 0.32 + 0.26 * t
        channel = rng.choice(["Online", "Retail Store", "Wholesale"],
                             p=[online_share, 0.83 - online_share, 0.17])
        segment = rng.choice(SEGMENTS, p=[0.55, 0.30, 0.15])

        units = int(rng.integers(lo, hi + 1)) * (int(rng.integers(4, 9)) if channel == "Wholesale" else 1)
        price = round(base_price * rng.normal(1, 0.04), 2)
        discount_options = [0, 0.05, 0.10, 0.15, 0.20]
        discount_p = [0.15, 0.2, 0.3, 0.2, 0.15] if region == "South" or channel == "Wholesale" else \
                     [0.45, 0.25, 0.18, 0.08, 0.04]
        discount = float(rng.choice(discount_options, p=discount_p))
        revenue = round(units * price * (1 - discount), 2)
        cost = round(units * base_price * cost_ratio * rng.normal(1, 0.03), 2)
        returned = rng.random() < return_rate * (1.6 if channel == "Online" else 1)

        customer = customers[n]
        records.append({
            "Order ID": f"ORD-{100001 + n}",
            "Customer ID": f"CUST-{10001 + customer}",
            "Customer Email": _email(customer),
            "Order Date": date.strftime("%b %d, %Y") if rng.random() < 0.15 else date.strftime("%Y-%m-%d"),
            "Region": _messy_case(region, rng),
            "Sales Channel": channel,
            "Customer Segment": segment if rng.random() > 0.015 else rng.choice(["N/A", "", "unknown"]),
            "Category": category,
            "Product": product,
            "Units": units,
            "Unit Price ": price,
            "Discount": f"{int(discount * 100)}%",
            "Revenue": f"${revenue:,.2f}" if rng.random() < 0.3 else f"{revenue:.2f}",
            "Cost": cost if rng.random() > 0.01 else "",
            "Returned": "Yes" if returned else "No",
        })

    df = pd.DataFrame(records)
    duplicates = df.sample(25, random_state=SEED)
    blanks = pd.DataFrame([{c: "" for c in df.columns}] * 3)
    df = pd.concat([df, duplicates, blanks], ignore_index=True)
    return df.sample(frac=1, random_state=SEED).reset_index(drop=True)


def _assign_customers(order_days: pd.DatetimeIndex, rng: np.random.Generator) -> list[int]:
    """One customer per order, drawn from a base that joins, repeats and churns.

    Each new customer is given a geometric lifetime in months and added to the roster of
    every month it covers. An order then picks from whoever is still active that month,
    which produces a cohort grid with a real decay curve instead of a uniform sprinkle.
    """
    months = pd.PeriodIndex(order_days, freq="M")
    span = range(months.min().ordinal, months.max().ordinal + 1)
    roster: dict[int, list[int]] = {ordinal: [] for ordinal in span}
    assignments: list[int] = []
    next_id = 0

    for month in months:
        pool = roster[month.ordinal]
        if not pool or rng.random() < NEW_CUSTOMER_RATE:
            lifetime = int(rng.geometric(CHURN_RATE))
            for step in range(lifetime):
                target = month.ordinal + step
                if target in roster:
                    roster[target].append(next_id)
            next_id += 1
            pool = roster[month.ordinal]
        assignments.append(int(pool[rng.integers(len(pool))]))
    return assignments


def _email(customer: int) -> str:
    first = FIRST_NAMES[customer % len(FIRST_NAMES)]
    last = LAST_NAMES[(customer // len(FIRST_NAMES)) % len(LAST_NAMES)]
    return f"{first.lower()}.{last.lower()}{customer}@example.com"


def _messy_case(value: str, rng: np.random.Generator) -> str:
    roll = rng.random()
    if roll < 0.03:
        return value.upper()
    if roll < 0.05:
        return value.lower()
    if roll < 0.07:
        return f" {value} "
    return value


if __name__ == "__main__":
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    frame = generate()
    frame.to_csv(OUTPUT, index=False)
    print(f"Wrote {len(frame):,} rows to {OUTPUT}")
