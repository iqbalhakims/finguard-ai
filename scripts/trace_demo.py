"""Play with Tempo: send a few traced "checkouts" from your laptop through the
FinGuard API and get trace IDs to open in Grafana.

Each checkout is ONE distributed trace:
  laptop  demo.checkout ─┬─ card.authorise ── HTTP POST /predict ──► finguard-api (in DOKS)
                         │                                          ├─ features.build
                         │                                          ├─ model.predict
                         │                                          ├─ shap.explain
                         │                                          └─ drift.observe
                         └─ (decision recorded on the laptop span)
Laptop spans go to https://otel.iqbalhakim.site (bearer token); the API's spans go
through the in-cluster collector. The W3C traceparent header links them, and its
"sampled" flag makes the API trace these requests even though it samples only 10%.

    make doks-forward                      # terminal 1: API on localhost:8080
    .venv/bin/python scripts/trace_demo.py # terminal 2
"""

from __future__ import annotations

import argparse
import json
import random
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from opentelemetry import propagate, trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

NORMAL = {
    "merchant_category": "grocery", "channel": "pos", "card_present": True, "is_international": False,
    "new_device": False, "distance_from_home_km": 3.2, "amount": 42.5, "avg_amount_30d": 48.0,
    "txn_count_1h": 0, "txn_count_24h": 2, "amount_sum_24h": 61.0, "seconds_since_last_txn": 14400,
}
SUSPICIOUS = {
    "merchant_category": "electronics", "channel": "online", "card_present": False, "is_international": True,
    "new_device": True, "distance_from_home_km": 6800.0, "amount": 1249.99, "avg_amount_30d": 58.2,
    "txn_count_1h": 3, "txn_count_24h": 4, "amount_sum_24h": 310.5, "seconds_since_last_txn": 420,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", default="http://localhost:8080", help="FinGuard API (port-forward)")
    parser.add_argument("--otel", default="https://otel.iqbalhakim.site", help="public OTLP/HTTP endpoint")
    parser.add_argument("--token-file", type=Path, default=Path("k8s/.secrets/otel-ingest-token"))
    parser.add_argument("--count", type=int, default=4)
    args = parser.parse_args()

    token = args.token_file.read_text().strip()
    provider = TracerProvider(resource=Resource.create({"service.name": "laptop-demo"}))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{args.otel}/v1/traces", headers={"Authorization": f"Bearer {token}"}))
    )
    trace.set_tracer_provider(provider)
    tracer = trace.get_tracer("trace-demo")

    for i in range(args.count):
        kind = "suspicious" if i % 2 else "normal"
        txn = {
            **(SUSPICIOUS if kind == "suspicious" else NORMAL),
            "transaction_id": f"demo-{int(time.time())}-{i}",
            "customer_id": "cust_demo",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with tracer.start_as_current_span("demo.checkout", attributes={"demo.kind": kind}) as checkout:
            with tracer.start_as_current_span("card.authorise", kind=trace.SpanKind.CLIENT) as call:
                headers = {"Content-Type": "application/json"}
                propagate.inject(headers)  # adds traceparent (sampled) -> the API joins this trace
                req = urllib.request.Request(
                    f"{args.api}/predict?explain=true", data=json.dumps(txn).encode(), headers=headers, method="POST"
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    result = json.loads(resp.read())
                call.set_attribute("http.response.status_code", resp.status)
            checkout.set_attribute("finguard.decision", result["decision"])
            checkout.set_attribute("finguard.fraud_probability", result["fraud_probability"])
            trace_id = format(checkout.get_span_context().trace_id, "032x")
        print(f"{kind:<10} decision={result['decision']:<7} p={result['fraud_probability']:.3f}  trace_id={trace_id}")
        time.sleep(random.uniform(0.2, 0.6))

    provider.shutdown()  # flush spans to otel.iqbalhakim.site
    print("\nGrafana -> Explore -> Tempo -> TraceQL: paste a trace_id, or try "
          '{resource.service.name="laptop-demo"}')


if __name__ == "__main__":
    main()
