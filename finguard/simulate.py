"""Traffic simulator: streams synthetic transactions to the inference API.

Because the simulator knows the ground-truth labels, it also prints live
precision/recall — handy for watching the model behave under drift.

    python -m finguard.simulate --url http://localhost:8000 --rps 20
    python -m finguard.simulate --url http://localhost:8000 --rps 20 --drift

Environment variables (used by the Docker / Kubernetes deployments):
SIMULATOR_URL, SIMULATOR_RPS, SIMULATOR_DRIFT=true|false, SIMULATOR_EXPLAIN_RATE.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
from opentelemetry import propagate, trace

from finguard.api import telemetry
from finguard.data.generate import BASELINE, DRIFTED, generate_transactions
from finguard.data.process import process

tracer = trace.get_tracer("finguard.simulate")

PAYLOAD_FIELDS = [
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
    "txn_count_1h",
    "txn_count_24h",
    "amount_sum_24h",
    "avg_amount_30d",
    "seconds_since_last_txn",
]


def _env_bool(name: str) -> bool:
    return os.getenv(name, "false").lower() in {"1", "true", "yes"}


def _clean(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, float) and math.isnan(value):
        return None
    if hasattr(value, "item"):  # numpy scalar
        return value.item()
    return value


def transaction_stream(drift: bool, seed: int):
    """Endless generator of (payload, is_fraud) in time order."""
    scenario = DRIFTED if drift else BASELINE
    while True:
        raw = generate_transactions(n_customers=600, days=45, start="2026-07-01", seed=seed, scenario=scenario)
        df, _ = process(raw)
        # Skip the first 3 weeks: they only exist to give every customer a realistic
        # history, otherwise the stream would be full of cold-start transactions.
        df = df[df["timestamp"] >= df["timestamp"].min() + pd.Timedelta(days=21)]
        for row in df[PAYLOAD_FIELDS + ["is_fraud"]].to_dict("records"):
            label = int(row.pop("is_fraud"))
            yield {k: _clean(v) for k, v in row.items()}, label
        seed += 1


def wait_until_ready(url: str, timeout: float = 120) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/health/ready", timeout=3) as r:
                if r.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(2)
    raise SystemExit(f"API at {url} not ready after {timeout}s")


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.c = Counter()

    def add(self, **kw):
        with self.lock:
            self.c.update(kw)

    def line(self) -> str:
        with self.lock:
            c = dict(self.c)
        tp, fp, fn = c.get("tp", 0), c.get("fp", 0), c.get("fn", 0)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        return (
            f"sent={c.get('sent', 0)} errors={c.get('errors', 0)} "
            f"approve={c.get('approve', 0)} review={c.get('review', 0)} block={c.get('block', 0)} "
            f"| live precision={precision:.2f} recall={recall:.2f} (fraud seen={tp + fn})"
        )


def send(url: str, payload: dict, label: int, explain: bool, stats: Stats) -> None:
    endpoint = f"{url}/predict" + ("?explain=true" if explain else "")
    # The simulator plays the card-authorisation system: a CLIENT span per call, with
    # its trace context (traceparent) passed on, so simulator -> API forms one trace
    # and an edge in the Tempo service graph. No-op unless tracing is configured.
    with tracer.start_as_current_span(
        "card.authorise",
        kind=trace.SpanKind.CLIENT,
        attributes={"http.request.method": "POST", "url.full": endpoint, "server.address": url},
    ) as span:
        headers = {"Content-Type": "application/json"}
        propagate.inject(headers)
        req = urllib.request.Request(endpoint, data=json.dumps(payload).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                span.set_attribute("http.response.status_code", r.status)
                result = json.loads(r.read())
        except (urllib.error.URLError, OSError, ValueError) as exc:
            span.record_exception(exc)
            span.set_status(trace.Status(trace.StatusCode.ERROR, type(exc).__name__))
            stats.add(sent=1, errors=1)
            return
        span.set_attribute("finguard.decision", result["decision"])
    flagged = result["is_fraud"]
    stats.add(
        sent=1,
        **{result["decision"]: 1},
        tp=int(flagged and label == 1),
        fp=int(flagged and label == 0),
        fn=int(not flagged and label == 1),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.getenv("SIMULATOR_URL", "http://localhost:8000"))
    parser.add_argument("--rps", type=float, default=float(os.getenv("SIMULATOR_RPS", "10")))
    parser.add_argument("--drift", action="store_true", default=_env_bool("SIMULATOR_DRIFT"))
    parser.add_argument("--explain-rate", type=float, default=float(os.getenv("SIMULATOR_EXPLAIN_RATE", "0.05")))
    parser.add_argument("--count", type=int, default=0, help="stop after N requests (0 = run forever)")
    parser.add_argument("--seed", type=int, default=int(os.getenv("SIMULATOR_SEED", "1000")))
    args = parser.parse_args()

    url = args.url.rstrip("/")
    if telemetry.setup_provider("traffic-simulator"):
        print("tracing card.authorise calls (OTLP)", flush=True)
    print(f"waiting for {url} ...", flush=True)
    wait_until_ready(url)
    print(f"streaming {'DRIFTED' if args.drift else 'baseline'} traffic at {args.rps} req/s", flush=True)

    stats = Stats()
    rng = random.Random(args.seed)
    interval = 1.0 / args.rps
    next_report = time.time() + 10
    next_send = time.perf_counter()
    with ThreadPoolExecutor(max_workers=16) as pool:
        for i, (payload, label) in enumerate(transaction_stream(args.drift, args.seed)):
            if args.count and i >= args.count:
                break
            pool.submit(send, url, payload, label, rng.random() < args.explain_rate, stats)
            next_send += interval
            delay = next_send - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            if time.time() >= next_report:
                print(stats.line(), flush=True)
                next_report = time.time() + 10
    print(stats.line(), flush=True)
    getattr(trace.get_tracer_provider(), "shutdown", lambda: None)()  # flush spans on --count runs


if __name__ == "__main__":
    main()
