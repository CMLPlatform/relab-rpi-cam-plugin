"""Minimal OpenTelemetry setup for the plugin."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

if TYPE_CHECKING:
    from fastapi import FastAPI


class _FastAPIInstrumentorProtocol(Protocol):
    def instrument_app(self, app: FastAPI, *, tracer_provider: TracerProvider) -> None: ...
    def uninstrument_app(self, app: FastAPI) -> None: ...


class _HTTPXInstrumentorProtocol(Protocol):
    def instrument(self, *, tracer_provider: TracerProvider) -> None: ...
    def uninstrument(self) -> None: ...

logger = logging.getLogger(__name__)


class ObservabilityHandle:
    """Tracks active OTel instrumentation so shutdown can clean up."""

    def __init__(
        self,
        *,
        fastapi_instrumentor: _FastAPIInstrumentorProtocol,
        httpx_instrumentor: _HTTPXInstrumentorProtocol,
        tracer_provider: TracerProvider,
    ) -> None:
        self.fastapi_instrumentor = fastapi_instrumentor
        self.httpx_instrumentor = httpx_instrumentor
        self.tracer_provider = tracer_provider

    def shutdown(self, app: FastAPI) -> None:
        """Best-effort teardown for active OTel instrumentation."""
        try:
            self.fastapi_instrumentor.uninstrument_app(app)
        except Exception:
            logger.exception("Failed to uninstrument FastAPI OTel hooks")
        try:
            self.httpx_instrumentor.uninstrument()
        except Exception:
            logger.exception("Failed to uninstrument HTTPX OTel hooks")
        try:
            self.tracer_provider.shutdown()
        except Exception:
            logger.exception("Failed to shut down OTel tracer provider")


def setup_observability(
    app: FastAPI,
    *,
    enabled: bool,
    service_name: str,
    otlp_endpoint: str,
) -> ObservabilityHandle | None:
    """Configure opt-in FastAPI/httpx tracing when OTLP export is enabled."""
    if not enabled or not otlp_endpoint:
        return None

    resource = Resource.create({"service.name": service_name})
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint)))
    trace.set_tracer_provider(tracer_provider)

    fastapi_instrumentor = FastAPIInstrumentor()
    fastapi_instrumentor.instrument_app(app, tracer_provider=tracer_provider)

    httpx_instrumentor = HTTPXClientInstrumentor()
    httpx_instrumentor.instrument(tracer_provider=tracer_provider)

    logger.info(
        "OpenTelemetry tracing enabled for service=%s endpoint=%s",
        service_name,
        otlp_endpoint,
    )
    return ObservabilityHandle(
        fastapi_instrumentor=fastapi_instrumentor,
        httpx_instrumentor=httpx_instrumentor,
        tracer_provider=tracer_provider,
    )
