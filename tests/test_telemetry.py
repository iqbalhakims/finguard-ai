import os
import subprocess
import sys
import textwrap

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from finguard.api import telemetry
from finguard.api.app import FraudService
from finguard.api.schemas import Transaction
from finguard.model import ModelBundle

_exporter = InMemorySpanExporter()
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(_exporter))
trace.set_tracer_provider(_provider)


def test_tracing_is_off_without_an_endpoint(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    assert not telemetry.enabled()


def test_scoring_emits_ml_spans(bundle_dir, txn):
    _exporter.clear()
    service = FraudService(ModelBundle.load(bundle_dir))
    with telemetry.tracer.start_as_current_span("request"):
        service.score([Transaction(**txn)], explain=True)
    spans = {s.name: s for s in _exporter.get_finished_spans()}
    assert {"features.build", "model.predict", "shap.explain", "drift.observe", "request"} <= set(spans)
    assert spans["model.predict"].attributes["finguard.model_version"] == "test"
    assert spans["request"].attributes["finguard.decision"] in {"approve", "review", "block"}
    parent = spans["request"].context.span_id
    assert all(spans[n].parent.span_id == parent for n in ("features.build", "model.predict", "shap.explain"))


def test_log_records_carry_trace_ids():
    import logging

    record = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", None, None)
    with telemetry.tracer.start_as_current_span("s") as span:
        telemetry.TraceContextFilter().filter(record)
        assert record.trace_id == format(span.get_span_context().trace_id, "032x")
    telemetry.TraceContextFilter().filter(record)
    assert record.trace_id == "-"


def test_latency_exemplars_link_to_traces(bundle_dir, txn):
    """End to end in a fresh process: OTel middleware outermost -> exemplar has the trace id."""
    script = textwrap.dedent(
        f"""
        import json
        from fastapi.testclient import TestClient
        import finguard.api.app as api
        with TestClient(api.app) as c:
            assert c.post("/predict", json={txn!r}).status_code == 200
            body = c.get("/metrics", headers={{"Accept": "application/openmetrics-text; version=1.0.0"}}).text
        line = [l for l in body.splitlines() if l.startswith("finguard_http_request_duration_seconds_bucket") and 'route="/predict"' in l and "trace_id" in l]
        print("EXEMPLAR" if line else "MISSING")
        """
    )
    env = {
        **os.environ,
        "FINGUARD_MODEL_DIR": str(bundle_dir),
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:9",  # nothing listens: export fails quietly
        "OTEL_BSP_EXPORT_TIMEOUT": "100",
    }
    out = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=120)
    assert "EXEMPLAR" in out.stdout, out.stdout + out.stderr[-2000:]


def test_simulator_calls_are_client_spans_with_trace_context():
    """The simulator's call becomes a CLIENT span whose context reaches the API (service-graph edge)."""
    import json as _json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from finguard import simulate

    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["traceparent"] = self.headers.get("traceparent")
            self.rfile.read(int(self.headers["Content-Length"]))
            body = _json.dumps({"decision": "block", "is_fraud": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()
    _exporter.clear()
    stats = simulate.Stats()
    simulate.send(f"http://127.0.0.1:{server.server_port}", {"transaction_id": "t"}, 1, False, stats)
    server.server_close()

    span = next(s for s in _exporter.get_finished_spans() if s.name == "card.authorise")
    assert span.kind == trace.SpanKind.CLIENT
    assert span.attributes["finguard.decision"] == "block"
    trace_id = format(span.context.trace_id, "032x")
    assert seen["traceparent"] and seen["traceparent"].split("-")[1] == trace_id
    assert stats.c["block"] == 1 and stats.c["tp"] == 1
