"""OpenTelemetry tracing for the inference API.

Off by default. Tracing is enabled when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set
(e.g. ``http://otel-collector.observability:4318``); everything else uses the
standard OTEL_* environment variables (service name, sampler, resource attributes).

What it adds:
  * a server span per HTTP request (FastAPI auto-instrumentation, health/metrics excluded)
  * child spans for the ML steps: features.build, model.predict, shap.explain, drift.observe
  * trace_id / span_id on every log line
  * trace_id exemplars on the request-latency histogram, so Grafana can jump from a
    latency spike to the trace behind it
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from opentelemetry import trace

from finguard import __version__

logger = logging.getLogger("finguard.api")
tracer = trace.get_tracer("finguard.api", __version__)

EXCLUDED_URLS = "health/live,health/ready,metrics"


def enabled() -> bool:
    return bool(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"))


def setup(app: FastAPI) -> None:
    """Configure the global tracer provider and instrument ``app`` (call before startup)."""
    if not enabled():
        return
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create(
        {
            SERVICE_NAME: os.getenv("OTEL_SERVICE_NAME", "finguard-api"),
            SERVICE_VERSION: __version__,
        }
    )
    provider = TracerProvider(resource=resource)  # sampler from OTEL_TRACES_SAMPLER(_ARG)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, excluded_urls=EXCLUDED_URLS)
    logger.info("OpenTelemetry tracing enabled -> %s", os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"))


def current_trace_id() -> str | None:
    """32-hex trace id of the active, sampled span (for exemplars), else None."""
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid and ctx.trace_flags.sampled:
        return format(ctx.trace_id, "032x")
    return None


class TraceContextFilter(logging.Filter):
    """Adds trace_id / span_id to log records ("-" outside a trace)."""

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = trace.get_current_span().get_span_context()
        record.trace_id = format(ctx.trace_id, "032x") if ctx.is_valid else "-"
        record.span_id = format(ctx.span_id, "016x") if ctx.is_valid else "-"
        return True
