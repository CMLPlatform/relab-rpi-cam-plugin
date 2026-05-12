# Architecture

This document is for maintainers and agents changing the plugin internals. Operator setup lives in [INSTALL.md](INSTALL.md); contributor workflow lives in [CONTRIBUTING.md](CONTRIBUTING.md).

The plugin is a FastAPI app running on a Raspberry Pi. It captures images and video, serves local setup and preview surfaces, opens an outbound relay to RELab, and uploads captures either to the RELab backend or to S3-compatible storage.

## Request Flow

```text
RELab backend / local browser
        |
        | outbound WebSocket relay or local HTTP
        v
FastAPI app (app/main.py, app/router.py)
        |
        v
Feature routers: camera / pairing / auth / system / frontend
        |
        v
Feature services and shared infrastructure
        |
        v
AppRuntime-owned services, workers, and runtime state
```

Feature packages own their routers, schemas, dependencies, exceptions, and services. Cross-cutting packages at `app/` root own infrastructure: backend client, relay, media pipeline, upload queue, image sinks, observability, and workers.

## Runtime Container

`app/core/runtime.py::AppRuntime` is the process container. It owns:

- camera manager
- preview pipeline and media helpers
- relay service and relay state
- pairing service and pairing state
- upload queue worker
- thermal, preview-sleep, and thumbnail workers
- observability handle
- managed background task sets

Code that runs outside a request, such as pairing and relay startup, accesses the active runtime through `app/core/runtime_context.py`.

## Lifespan And Bootstrap

Startup is coordinated through `app/core/lifespan.py`, `app/core/bootstrap.py`, and `app/core/settings.py`:

1. Load env-backed `Settings`.
1. Apply persisted relay credentials from `~/.config/relab/relay_credentials.json`.
1. Ensure local and relay-local API keys exist where needed.
1. Build `AppRuntime` and register it as the active runtime.
1. Start managed background workers, pairing, and relay tasks according to config.

Shutdown cancels managed tasks and closes camera, relay, and observability resources.

## Configuration And Runtime State

`Settings` is static operator configuration loaded from `.env`. `RuntimeState` stores values that are generated, persisted, or mutable at runtime, such as relay credentials, local API keys, relay connection state, and derived authorized key snapshots.

Configuration precedence at startup is:

1. env-backed settings
1. persisted runtime credentials
1. generated local defaults

Transport policy is enforced at the settings/runtime boundary. Relay URLs use `wss://` outside `APP_ENV=development`. Remote production S3 and OTLP endpoints use HTTPS; loopback HTTP and development HTTP are accepted for local setups.

## Auth Boundaries

The plugin has two request-auth modes:

- `X-API-Key` for local direct clients, relay-dispatched local calls, and scriptable integrations
- browser session cookie for the local operator UI

Protected feature routers are included with the shared `verify_request` dependency. Unsafe cookie-authenticated writes require same-origin browser proof through `Origin` or `Referer`; explicit API-key writes do not use that browser CSRF path.

Local login attempts are rate-limited at the auth boundary. Request schemas own edge validation for camera controls, focus mode consistency, stream keys, upload metadata, and other bounded user-controlled payloads before work reaches services.

The setup page is intentionally public during pairing so headless operators can read the pairing code. The pairing code is a short-lived bootstrap credential, so `/setup` and pairing logs are local/operator-only surfaces during the pairing window.

## Pairing

When relay credentials are absent and `PAIRING_BACKEND_URL` is set, `app/pairing/services/service.py` enters pairing mode:

1. generate a short pairing code and device fingerprint
1. register with the pairing backend
1. display the code on `/setup` and in the `PAIRING READY` log banner
1. poll until the backend claims the code
1. validate returned relay transport
1. persist credentials through `app/pairing/services/credentials.py`
1. start relay operation

Pairing rotation replaces the active code without deleting existing relay credentials.

## Relay

`app/relay/service.py` keeps one outbound WebSocket connection to the paired backend. The backend is a privileged command source for the device. The Pi-side relay limits blast radius with:

- explicit method/path allowlists
- relative path rejection
- filtered inbound headers
- local relay API-key injection before dispatch to FastAPI
- structural command envelope validation plus bounded frame size, queue depth, and command concurrency
- exponential reconnect

New relay-reachable routes should be intentionally added to the allowlist and covered by tests.

## Camera, Preview, And Capture

`app/camera/services/manager.py` coordinates camera operations behind a lock. The active backend is selected through the camera backend interface; production uses Picamera2/libcamera and tests use fakes.

Preview uses a local MediaMTX sidecar and LL-HLS. Worker-owned hibernation stops the low-resolution encoder after relay idleness and restarts it on demand. The thumbnail worker keeps the setup UI preview current while preview is active.

Capture requests produce image bytes and bounded metadata, then pass them to the configured `ImageSink`.

## Image Sinks And Upload Queue

`app/image_sinks/base.py::ImageSink` abstracts capture persistence:

- backend sink uploads to the paired RELab backend
- S3 sink uploads to an S3-compatible bucket

`IMAGE_SINK=auto` infers the sink from config. Explicit sink configuration fails loudly when required fields are missing. The upload queue is sink-agnostic: failed synchronous uploads are persisted to disk, retried with exponential backoff, and dead-lettered after exhaustion. Queue capacity and dead-letter retention settings are the single retention policy for failed local captures.

Captured images, queue entries, dead letters, and cached preview thumbnails are sensitive device data. Successful captures remove their temporary local JPEG after upload; runtime cleanup also covers cached preview thumbnails. Standalone S3 public URL generation depends on operator-controlled bucket and proxy policy, so public object URLs are an explicit standalone storage choice rather than a backend-paired privacy boundary.

## Observability

Structured logging is always enabled. Optional components are configured by env:

- Loki-compatible log shipping through the `observability-ship` Compose profile
- OTLP tracing through `OTEL_ENABLED=true` and `OTEL_EXPORTER_OTLP_ENDPOINT`
- local telemetry collection exposed through protected system routes

Tracing setup lives in `app/observability/tracing.py`; logging and request-id context live in `app/observability/logging.py` and `app/core/middleware.py`.

## Shared Contract Package

`relab_rpi_cam_models` contains the backend-to-plugin device DTOs. It validates pairing bootstrap payloads, local access bootstrap data, upload acknowledgements, and relay command wire shape. Command authorization policy and Pi receiver allowlists live in the main platform backend. Plugin runtime logic stays in `app/`; frontend code consumes backend OpenAPI rather than importing device DTOs directly.
