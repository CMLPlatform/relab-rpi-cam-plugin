"""Tests for optional OpenTelemetry setup."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from fastapi import FastAPI

from app.utils import observability as observability_mod

if TYPE_CHECKING:
    import pytest
    from opentelemetry.sdk.trace import TracerProvider

FASTAPI_TEARDOWN_ERROR = "fastapi teardown failed"
HTTPX_TEARDOWN_ERROR = "httpx teardown failed"
PROVIDER_TEARDOWN_ERROR = "provider shutdown failed"
FASTAPI_TEARDOWN_LOG = "Failed to uninstrument FastAPI OTel hooks"
HTTPX_TEARDOWN_LOG = "Failed to uninstrument HTTPX OTel hooks"
PROVIDER_TEARDOWN_LOG = "Failed to shut down OTel tracer provider"


class _RecordingFastAPIInstrumentor:
    def __init__(self) -> None:
        self.instrumented: list[tuple[FastAPI, object]] = []
        self.uninstrumented: list[FastAPI] = []

    def instrument_app(self, app: FastAPI, *, tracer_provider: object) -> None:
        self.instrumented.append((app, tracer_provider))

    def uninstrument_app(self, app: FastAPI) -> None:
        self.uninstrumented.append(app)


class _RecordingHTTPXInstrumentor:
    def __init__(self) -> None:
        self.instrumented_with: list[object] = []
        self.uninstrument_calls = 0

    def instrument(self, *, tracer_provider: object) -> None:
        self.instrumented_with.append(tracer_provider)

    def uninstrument(self) -> None:
        self.uninstrument_calls += 1


class _ExplodingFastAPIInstrumentor(_RecordingFastAPIInstrumentor):
    def uninstrument_app(self, app: FastAPI) -> None:
        del app
        raise RuntimeError(FASTAPI_TEARDOWN_ERROR)


class _ExplodingHTTPXInstrumentor(_RecordingHTTPXInstrumentor):
    def uninstrument(self) -> None:
        raise RuntimeError(HTTPX_TEARDOWN_ERROR)


class _RecordingTracerProvider:
    def __init__(self, resource: object) -> None:
        self.resource = resource
        self.span_processors: list[object] = []
        self.shutdown_calls = 0

    def add_span_processor(self, processor: object) -> None:
        self.span_processors.append(processor)

    def shutdown(self) -> None:
        self.shutdown_calls += 1


class _ExplodingTracerProvider(_RecordingTracerProvider):
    def shutdown(self) -> None:
        raise RuntimeError(PROVIDER_TEARDOWN_ERROR)


class TestSetupObservability:
    """Tests for opt-in OTel configuration."""

    def test_disabled_returns_none(self) -> None:
        """Disabled observability should skip all OTel setup."""
        app = FastAPI()
        handle = observability_mod.setup_observability(
            app,
            enabled=False,
            service_name="test-service",
            otlp_endpoint="",
        )

        assert handle is None

    def test_missing_endpoint_returns_none(self) -> None:
        """Missing OTLP endpoint should keep observability disabled."""
        app = FastAPI()
        handle = observability_mod.setup_observability(
            app,
            enabled=True,
            service_name="test-service",
            otlp_endpoint="",
        )

        assert handle is None

    def test_enabled_instruments_fastapi_httpx_and_tracing(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Enabled observability should build and wire the tracing stack."""
        app = FastAPI()
        created_resources: list[dict[str, str]] = []
        exporters: list[dict[str, str]] = []
        processors: list[dict[str, object]] = []
        tracer_providers: list[_RecordingTracerProvider] = []
        set_provider_calls: list[object] = []
        fastapi_instrumentor = _RecordingFastAPIInstrumentor()
        httpx_instrumentor = _RecordingHTTPXInstrumentor()

        monkeypatch.setattr(
            observability_mod.Resource,
            "create",
            lambda attrs: created_resources.append(attrs) or {"resource": attrs},
        )

        def _make_provider(*, resource: object) -> _RecordingTracerProvider:
            provider = _RecordingTracerProvider(resource)
            tracer_providers.append(provider)
            return provider

        monkeypatch.setattr(observability_mod, "TracerProvider", _make_provider)
        monkeypatch.setattr(
            observability_mod,
            "OTLPSpanExporter",
            lambda *, endpoint: exporters.append({"endpoint": endpoint}) or {"endpoint": endpoint},
        )
        monkeypatch.setattr(
            observability_mod,
            "BatchSpanProcessor",
            lambda exporter: processors.append({"exporter": exporter}) or {"processor_for": exporter},
        )
        monkeypatch.setattr(observability_mod.trace, "set_tracer_provider", set_provider_calls.append)
        monkeypatch.setattr(observability_mod, "FastAPIInstrumentor", lambda: fastapi_instrumentor)
        monkeypatch.setattr(observability_mod, "HTTPXClientInstrumentor", lambda: httpx_instrumentor)

        handle = observability_mod.setup_observability(
            app,
            enabled=True,
            service_name="camera-plugin",
            otlp_endpoint="http://otel-collector:4318/v1/traces",
        )

        assert created_resources == [{"service.name": "camera-plugin"}]
        assert exporters == [{"endpoint": "http://otel-collector:4318/v1/traces"}]
        assert processors == [{"exporter": {"endpoint": "http://otel-collector:4318/v1/traces"}}]
        assert len(tracer_providers) == 1
        provider = tracer_providers[0]
        assert provider.resource == {"resource": {"service.name": "camera-plugin"}}
        assert provider.span_processors == [{"processor_for": {"endpoint": "http://otel-collector:4318/v1/traces"}}]
        assert set_provider_calls == [provider]
        assert fastapi_instrumentor.instrumented == [(app, provider)]
        assert httpx_instrumentor.instrumented_with == [provider]
        assert isinstance(handle, observability_mod.ObservabilityHandle)
        assert handle.tracer_provider is provider

    def test_shutdown_tears_down_all_instrumentation(self) -> None:
        """Shutdown should call both instrumentors and the tracer provider."""
        app = FastAPI()
        fastapi_instrumentor = _RecordingFastAPIInstrumentor()
        httpx_instrumentor = _RecordingHTTPXInstrumentor()
        tracer_provider = _RecordingTracerProvider(resource={})
        handle = observability_mod.ObservabilityHandle(
            fastapi_instrumentor=fastapi_instrumentor,
            httpx_instrumentor=httpx_instrumentor,
            tracer_provider=cast("TracerProvider", tracer_provider),
        )

        handle.shutdown(app)

        assert fastapi_instrumentor.uninstrumented == [app]
        assert httpx_instrumentor.uninstrument_calls == 1
        assert tracer_provider.shutdown_calls == 1

    def test_shutdown_logs_each_teardown_failure(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Shutdown should keep going and log each teardown failure separately."""
        app = FastAPI()
        handle = observability_mod.ObservabilityHandle(
            fastapi_instrumentor=_ExplodingFastAPIInstrumentor(),
            httpx_instrumentor=_ExplodingHTTPXInstrumentor(),
            tracer_provider=cast("TracerProvider", _ExplodingTracerProvider(resource={})),
        )

        with caplog.at_level(logging.ERROR):
            handle.shutdown(app)

        assert FASTAPI_TEARDOWN_LOG in caplog.text
        assert HTTPX_TEARDOWN_LOG in caplog.text
        assert PROVIDER_TEARDOWN_LOG in caplog.text
