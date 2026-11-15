"""Synthetic card-transaction generator with realistic fraud patterns.

Real banking data is confidential, so the platform ships with a simulator that
produces a transaction log with the properties that make fraud detection hard:

* heavy class imbalance (~1% fraud),
* per-customer behaviour (spend level, active hours, channel mix, travel habits),
* several distinct fraud typologies, each with its own signature:
    - ``cnp``               card-not-present online fraud (new device, high value, night)
    - ``account_takeover``  bursts of online purchases within minutes
    - ``card_testing``      tiny "probe" charges followed by one large purchase
    - ``skimming``          cloned card used in person far from home / at ATMs
    - ``friendly_fraud``    a disputed purchase that looks exactly like normal behaviour
* overlap with legitimate behaviour (travellers, night owls, shopping sprees,
  big-ticket purchases, new phones, VPN/mobile IP geolocation noise), so the
  classes are not trivially separable.

A ``Scenario`` controls the population; ``DRIFTED`` shifts it to emulate a
changed production environment for the drift-detection demo.

Usage::

    python -m finguard.data.generate                 # baseline training data
    python -m finguard.data.generate --drift         # shifted "production" data
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from finguard.config import (
    CHANNELS,
    MERCHANT_CATEGORIES,
    RAW_DRIFTED_PATH,
    RAW_PATH,
    SEED,
)

CAT_INDEX = {c: i for i, c in enumerate(MERCHANT_CATEGORIES)}

# Base popularity of each merchant category and its typical ticket size
# relative to the customer's average spend.
CATEGORY_WEIGHTS = np.array([22, 16, 9, 10, 9, 2, 2, 6, 5, 4, 7, 8], dtype=float)
CATEGORY_AMOUNT_MULT = np.array([1.0, 0.7, 1.0, 1.3, 1.2, 4.0, 6.0, 0.8, 1.5, 1.2, 0.4, 2.0])


@dataclass(frozen=True)
class Scenario:
    name: str = "baseline"
    amount_multiplier: float = 1.0
    online_boost: float = 0.0
    international_boost: float = 0.0
    new_device_rate: float = 0.05
    activity_hour_shift: float = 0.0
    fraud_episodes_per_customer: float = 0.12


BASELINE = Scenario()
# "New mobile app + premium card launch": customers move online, re-register
# devices, spend more and travel more. Legitimate behaviour now resembles the
# fraud patterns the model learned, so false positives climb.
DRIFTED = Scenario(
    name="drifted",
    amount_multiplier=1.8,  # premium product mix / inflation
    online_boost=0.45,  # shift to e-commerce
    international_boost=0.10,
    new_device_rate=0.20,  # app migration -> many "new" devices
    activity_hour_shift=5.0,  # later activity pattern
    fraud_episodes_per_customer=0.20,
)


def _simulate_customers(rng: np.random.Generator, n: int) -> pd.DataFrame:
    prefs = rng.dirichlet(CATEGORY_WEIGHTS / CATEGORY_WEIGHTS.sum() * 25, size=n)
    night_owl = rng.random(n) < 0.08
    active_hour = np.where(night_owl, rng.uniform(21, 27, n), np.clip(rng.normal(14, 2.5, n), 8, 21))
    return pd.DataFrame(
        {
            "avg_amount": rng.lognormal(np.log(45), 0.6, n),
            "daily_rate": rng.gamma(2.0, 0.25, n),  # ~0.5 txns/day on average
            "active_hour": active_hour,
            "online_propensity": rng.beta(2, 5, n),
            "travel_prob": rng.beta(1, 60, n),
        }
    ).assign(prefs=list(prefs))


def _legit_transactions(
    rng: np.random.Generator,
    customers: pd.DataFrame,
    start: np.datetime64,
    days: int,
    scenario: Scenario,
) -> pd.DataFrame:
    counts = rng.poisson(customers["daily_rate"].to_numpy() * days)
    cust = np.repeat(np.arange(len(customers)), counts)
    m = len(cust)

    day = rng.integers(0, days, m)
    hour = (
        rng.normal(customers["active_hour"].to_numpy()[cust] + scenario.activity_hour_shift, 3.5)
        % 24
    )
    seconds = day * 86_400 + (hour * 3600).astype(np.int64)

    prefs = np.stack(customers["prefs"].to_numpy())
    cum = prefs.cumsum(axis=1)[cust]
    cat = np.minimum((rng.random(m)[:, None] > cum).sum(axis=1), len(MERCHANT_CATEGORIES) - 1)

    online_p = np.clip(customers["online_propensity"].to_numpy()[cust] + scenario.online_boost, 0, 0.95)
    channel = np.where(rng.random(m) < online_p * 0.6, "online", "pos").astype(object)
    channel[np.isin(cat, [CAT_INDEX["online_retail"], CAT_INDEX["digital_goods"]])] = "online"
    utilities = cat == CAT_INDEX["utilities"]
    channel[utilities] = np.where(rng.random(utilities.sum()) < 0.8, "online", "pos")
    travel = cat == CAT_INDEX["travel"]
    channel[travel] = np.where(rng.random(travel.sum()) < 0.6, "online", "pos")
    channel[cat == CAT_INDEX["cash_withdrawal"]] = "atm"

    amount = (
        customers["avg_amount"].to_numpy()[cust]
        * CATEGORY_AMOUNT_MULT[cat]
        * rng.lognormal(0, 0.55, m)
        * scenario.amount_multiplier
    )
    big_ticket = rng.random(m) < 0.004  # occasional legitimate splurge
    amount[big_ticket] *= rng.lognormal(1.5, 0.4, big_ticket.sum())

    intl_p = customers["travel_prob"].to_numpy()[cust] + scenario.international_boost
    intl_p = np.where(travel, intl_p + 0.25, intl_p)
    is_international = rng.random(m) < intl_p

    # Online "distance" comes from IP geolocation: VPNs and mobile carriers make it noisy.
    online_dist = np.where(rng.random(m) < 0.2, rng.exponential(500, m), rng.exponential(30, m))
    distance = np.where(channel == "online", online_dist, rng.exponential(6, m))
    trip = (channel != "online") & (rng.random(m) < 0.04)
    distance[trip] = rng.exponential(150, trip.sum())
    distance[is_international] = rng.uniform(400, 9000, is_international.sum())

    card_present = (channel != "online") & (rng.random(m) < 0.97)
    new_device = np.where(
        channel == "online",
        rng.random(m) < scenario.new_device_rate,
        rng.random(m) < 0.002,
    )

    legit = pd.DataFrame(
        {
            "cust": cust,
            "seconds": seconds,
            "amount": amount,
            "merchant_category": np.array(MERCHANT_CATEGORIES, dtype=object)[cat],
            "channel": channel,
            "card_present": card_present,
            "is_international": is_international,
            "new_device": new_device,
            "distance_from_home_km": distance,
            "is_fraud": 0,
            "fraud_type": "none",
        }
    )

    # Shopping sessions: legitimate bursts of 2-4 purchases within an hour.
    anchors = rng.choice(m, int(m * 0.12), replace=False)
    extra = legit.iloc[np.repeat(anchors, rng.integers(1, 4, len(anchors)))].copy()
    extra["seconds"] += rng.exponential(20 * 60, len(extra)).astype(np.int64)
    extra["amount"] *= rng.lognormal(0, 0.5, len(extra))
    return pd.concat([legit, extra], ignore_index=True)


def _fraud_episode(
    rng: np.random.Generator, kind: str, customer: pd.Series, t0: int, scenario: Scenario
) -> list[dict]:
    rows: list[dict] = []
    avg_amount = customer["avg_amount"]

    def add(offset_s: float, amount: float, category: str, channel: str, **kw) -> None:
        intl = kw.get("is_international", False)
        rows.append(
            {
                "seconds": int(t0 + offset_s),
                "amount": amount * scenario.amount_multiplier,
                "merchant_category": category,
                "channel": channel,
                "card_present": kw.get("card_present", False),
                "is_international": intl,
                "new_device": kw.get("new_device", False),
                "distance_from_home_km": kw.get(
                    "distance",
                    # half of attackers route through residential proxies near the victim
                    rng.uniform(400, 9000) if intl else rng.exponential(600 if rng.random() < 0.5 else 40),
                ),
                "is_fraud": 1,
                "fraud_type": kind,
            }
        )

    if kind == "cnp":
        offsets = np.cumsum(rng.exponential(40 * 60, rng.integers(1, 5)))
        for off in offsets:
            cat = rng.choice(["electronics", "online_retail", "digital_goods"], p=[0.45, 0.3, 0.25])
            add(
                off,
                avg_amount * rng.lognormal(1.3, 0.7),
                cat,
                "online",
                new_device=rng.random() < 0.6,
                is_international=rng.random() < 0.3,
            )
    elif kind == "account_takeover":
        offsets = np.cumsum(rng.exponential(8 * 60, rng.integers(4, 11)))
        cats = ["online_retail", "electronics", "digital_goods", "travel", "retail"]
        device = rng.random() < 0.7
        intl = rng.random() < 0.25
        for off in offsets:
            add(
                off,
                avg_amount * rng.lognormal(0.8, 0.7),
                rng.choice(cats),
                "online",
                new_device=device,
                is_international=intl,
            )
    elif kind == "card_testing":
        device = rng.random() < 0.6
        offsets = np.cumsum(rng.exponential(2 * 60, rng.integers(3, 9)))
        for off in offsets:
            add(off, rng.uniform(0.5, 4.99) / scenario.amount_multiplier, "digital_goods", "online", new_device=device)
        add(
            offsets[-1] + rng.exponential(15 * 60),
            avg_amount * rng.lognormal(1.5, 0.5),
            "electronics",
            "online",
            new_device=device,
        )
    elif kind == "skimming":
        intl = rng.random() < 0.4
        distance = rng.uniform(400, 9000) if intl else rng.uniform(80, 3000)
        offsets = np.cumsum(rng.exponential(90 * 60, rng.integers(2, 6)))
        for off in offsets:
            if rng.random() < 0.6:
                amt = max(100.0, round(avg_amount * rng.lognormal(1.4, 0.4), -1))
                add(off, amt, "cash_withdrawal", "atm", card_present=True, is_international=intl, distance=distance)
            else:
                cat = rng.choice(["retail", "electronics", "fuel"])
                add(
                    off,
                    avg_amount * rng.lognormal(1.0, 0.6),
                    cat,
                    "pos",
                    card_present=True,
                    is_international=intl,
                    distance=distance,
                )
    elif kind == "friendly_fraud":
        # Cardholder disputes a purchase they made: indistinguishable from normal
        # behaviour at authorisation time. Caps achievable recall, as in real life.
        prefs = customer["prefs"]
        cat = MERCHANT_CATEGORIES[min(int((rng.random() > prefs.cumsum()).sum()), len(prefs) - 1)]
        channel = "online" if cat in ("online_retail", "digital_goods") or rng.random() < 0.5 else "pos"
        if cat == "cash_withdrawal":
            channel = "atm"
        hour_s = (customer["active_hour"] % 24) * 3600 - (t0 % 86_400)
        add(
            hour_s + rng.normal(0, 2 * 3600),
            avg_amount * CATEGORY_AMOUNT_MULT[CAT_INDEX[cat]] * rng.lognormal(0.3, 0.6),
            cat,
            channel,
            card_present=channel != "online",
            distance=rng.exponential(30 if channel == "online" else 6),
        )
    else:  # pragma: no cover - guarded by caller
        raise ValueError(kind)
    return rows


def _fraud_transactions(
    rng: np.random.Generator,
    customers: pd.DataFrame,
    days: int,
    scenario: Scenario,
) -> pd.DataFrame:
    n_episodes = rng.poisson(scenario.fraud_episodes_per_customer * len(customers))
    victims = rng.integers(0, len(customers), n_episodes)
    kinds = rng.choice(
        ["cnp", "account_takeover", "card_testing", "skimming", "friendly_fraud"],
        p=[0.32, 0.22, 0.17, 0.17, 0.12],
        size=n_episodes,
    )
    rows: list[dict] = []
    for cust, kind in zip(victims, kinds):
        day = rng.integers(2, days)  # leave some history before the first attack
        # Fraudsters favour the small hours, but not exclusively.
        hour = rng.uniform(0, 6) if rng.random() < 0.55 else rng.uniform(0, 24)
        t0 = int(day * 86_400 + hour * 3600)
        for row in _fraud_episode(rng, kind, customers.iloc[cust], t0, scenario):
            row["cust"] = cust
            rows.append(row)
    return pd.DataFrame(rows)


def generate_transactions(
    n_customers: int = 4000,
    days: int = 120,
    start: str = "2026-03-01",
    seed: int = SEED,
    scenario: Scenario = BASELINE,
) -> pd.DataFrame:
    """Return a raw transaction log sorted by time."""
    rng = np.random.default_rng(seed)
    start_ts = np.datetime64(start, "s")
    customers = _simulate_customers(rng, n_customers)

    legit = _legit_transactions(rng, customers, start_ts, days, scenario)
    fraud = _fraud_transactions(rng, customers, days, scenario)
    df = pd.concat([legit, fraud], ignore_index=True)

    df = df[(df["seconds"] >= 0) & (df["seconds"] < days * 86_400)]  # bursts may spill past the window
    df["timestamp"] = start_ts + df["seconds"].to_numpy().astype("timedelta64[s]")
    df["amount"] = np.where(
        df["merchant_category"] == "cash_withdrawal",
        np.maximum(20, (df["amount"] / 10).round() * 10),
        np.maximum(0.5, df["amount"].round(2)),
    )
    df["distance_from_home_km"] = df["distance_from_home_km"].round(1)
    df["customer_id"] = [f"cust_{c:05d}" for c in df["cust"]]

    df = df.sort_values("timestamp", kind="stable").reset_index(drop=True)
    df.insert(0, "transaction_id", [f"txn_{scenario.name[:1]}{i:08d}" for i in range(len(df))])

    cols = [
        "transaction_id",
        "customer_id",
        "timestamp",
        "amount",
        "merchant_category",
        "channel",
        "card_present",
        "is_international",
        "new_device",
        "distance_from_home_km",
        "is_fraud",
        "fraud_type",
    ]
    df = df[cols].astype(
        {
            "card_present": bool,
            "is_international": bool,
            "new_device": bool,
            "is_fraud": "int8",
        }
    )
    assert set(df["channel"]) <= set(CHANNELS)
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--customers", type=int, default=4000)
    parser.add_argument("--days", type=int, default=120)
    parser.add_argument("--start", default=None, help="first day (YYYY-MM-DD)")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--drift", action="store_true", help="generate the shifted 'production' scenario")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    scenario = DRIFTED if args.drift else BASELINE
    start = args.start or ("2026-07-01" if args.drift else "2026-03-01")
    out = args.out or (RAW_DRIFTED_PATH if args.drift else RAW_PATH)
    seed = args.seed + (1 if args.drift else 0)

    df = generate_transactions(args.customers, args.days, start, seed, scenario)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)

    print(f"scenario       : {scenario.name}")
    print(f"transactions   : {len(df):,}")
    print(f"customers      : {df['customer_id'].nunique():,}")
    print(f"fraud rate     : {df['is_fraud'].mean():.3%}")
    print(f"period         : {df['timestamp'].min()} -> {df['timestamp'].max()}")
    print("fraud types    :", df.loc[df.is_fraud == 1, "fraud_type"].value_counts().to_dict())
    print(f"written to     : {out}")


if __name__ == "__main__":
    main()
