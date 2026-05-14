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

Architecture rewrites should keep this boundary explicit: `Settings` is static bootstrap input, `RuntimeState` is active mutable credential/auth state, and `AppRuntime` owns long-lived services, workers, and task lifecycles. A service should move into `AppRuntime` only when it is process-owned rather than request-local.

## Lifespan And Bootstrap

Startup is coordinated through `app/core/lifespan.py`, `app/core/bootstrap.py`, and `app/core/settings.py`:

1. Load env-backed `Settings`.
1. Build `AppRuntime` with a `RuntimeState` seeded from settings.
1. Apply persisted relay credentials from `~/.config/relab/relay_credentials.json` if static relay credentials are absent.
1. Ensure local and relay-local API keys exist where needed.
1. Register `AppRuntime` as the active runtime.
1. Start managed background workers, pairing, and relay tasks according to config.

Shutdown cancels managed tasks and closes camera, relay, and observability resources.

## Configuration And Runtime State

`Settings` is static operator configuration loaded from `.env`. `RuntimeState` stores values that are generated, persisted, or mutable at runtime, such as relay credentials, local API keys, and derived authorized key snapshots. `RelayRuntimeState` is separate and tracks relay connection/activity, not credentials.

Configuration precedence at startup is:

1. env-backed settings
1. persisted runtime credentials
1. generated local defaults

Transport policy is enforced at the settings/runtime boundary. Relay URLs use `wss://` outside `APP_ENV=development`. Remote S3, public URL templates, and OTLP endpoints use HTTPS unless they are loopback or development-only HTTP. Backend-returned media URLs are checked before they reach callers, so uploads do not come back as plain-HTTP links.

`APP_ENV=production` is the fail-closed default. Production rejects `DEBUG=true`, disables Pi-hosted Swagger/OpenAPI routes, blocks HTTP TRACE, and serves only `.css`, `.ico`, `.js`, and `.png`.

## External Connections

Outbound connections are fixed at startup and kept narrow:

- pairing API, relay WebSocket, and upload callbacks go to RELab
- relay local dispatch is allowlisted first, then gets the relay-local API key
- MediaMTX stays on localhost
- S3 uses HTTPS remotely
- observability collectors are optional

Any path used at request time is either encoded or checked against an allowlist first.

## Cryptography

Relay device assertions are short-lived ES256 JWTs signed with the Pi's P-256 key. We validate that key when settings load, credentials are saved, and runtime state is applied, so bad key types fail early.

Relay private keys, relay key IDs, camera IDs, and local API keys live in `~/.config/relab/relay_credentials.json`. The file is written atomically with `0600` permissions. Pairing creates a fresh key pair; unpairing deletes the file.

Session tokens, API keys, pairing fingerprints, JWT IDs, and capture IDs come from Python's `secrets` CSPRNG. API-key comparisons use `hmac.compare_digest`.

## Sensitive Data

Credentials such as relay keys, API keys, session tokens, pairing fingerprints, and S3 secrets stay out of URLs and query strings. API keys and assertions stay in headers or protected response bodies. Logout clears the browser session and drops cached state.

Responses use `Cache-Control: no-store`, static assets come from an explicit extension allowlist, and `/local-key` is only exposed explicitly.

Logs redact known secret fields, bearer tokens, and private keys before emission. Local pairing banners stay out of JSON logs. Captured images, queue entries, and preview thumbnails are bounded and validated before upload, retry, or serving.

## Auth Boundaries

The plugin has two request-auth modes:

- `X-API-Key` for local direct clients, relay-dispatched local calls, and scriptable integrations
- browser session cookie for the local operator UI

Protected feature routers are included with the shared `verify_request` dependency. Unsafe cookie-authenticated writes require same-origin browser proof through `Origin` or `Referer`; explicit API-key writes do not use that browser CSRF path.

Browser sessions are server-side, in-memory tokens for the local operator UI. Login replaces any existing cookie, sessions expire after 30 minutes of inactivity or 12 hours from creation, and logout or restart clears them.

The plugin does not manage local user accounts, admin roles, MFA, account-disable workflows, or federated identity sessions. Those controls live in the RELab platform/backend identity boundary. This device plugin only checks local API keys and its own short-lived operator UI sessions.

Local login attempts are rate-limited at the auth boundary. Request schemas own edge validation for camera controls, focus mode consistency, stream keys, upload metadata, and other bounded user-controlled payloads before work reaches services.

The setup page is intentionally public during pairing so headless operators can read the pairing code. Because that code is short-lived, `/setup` and pairing logs should be treated as operator-only during pairing.

Authorization is route based and local to the device. The RELab backend handles per-user and tenant authorization before sending relay commands; the plugin just keeps the device side narrow:

- public setup/status routes are `/`, `/setup`, `/pairing/state`, and local-network preview media
- protected routes include camera controls, captures, preview, streaming, telemetry, metrics, `local-key`, `local-access`, unpair, and pairing-code rotation
- relay-dispatched routes must match the allowlist in `relab_rpi_cam_models`

Sensitive details stay behind those boundaries. Public setup visitors see bootstrap state only, while authenticated operators can see topology and direct-access details. `/system/local-access` and `/local-key` expose the local API key after normal auth, and `/local-key` also requires a local-network client.

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

The relay allowlist is part of the private backend-to-plugin protocol. It belongs in the shared contract package (`relab_rpi_cam_models`) with the relay envelopes, not duplicated in plugin runtime code. Breaking relay protocol changes are allowed when they simplify the seam, but they require a shared package version bump and coordinated backend/plugin updates.

## Camera, Preview, And Capture

`app/camera/services/manager.py` coordinates camera operations behind a lock. The active backend is selected through the camera backend interface; production uses Picamera2/libcamera and tests use fakes.

Preview uses a local MediaMTX sidecar and LL-HLS. Worker-owned hibernation stops the low-resolution encoder after relay idleness and restarts it on demand. The thumbnail worker keeps the setup UI preview current while preview is active.

Capture requests produce image bytes and bounded metadata, then pass them to the configured `ImageSink`.

## Image Sinks And Upload Queue

`app/image_sinks/base.py::ImageSink` abstracts capture persistence:

- backend sink uploads to the paired RELab backend
- S3 sink uploads to an S3-compatible bucket

`IMAGE_SINK=auto` infers the sink from config. Explicit sink configuration fails loudly when required fields are missing. The upload queue is sink-agnostic: failed synchronous uploads are persisted to disk, retried with exponential backoff, and dead-lettered after exhaustion. Queue capacity and dead-letter retention settings are the single retention policy for failed local captures.

Capture IDs and filenames are generated internally. Current captures are `.jpg` / `image/jpeg`, bounded by `MAX_CAPTURE_PIXELS` and `MAX_CAPTURE_FILE_BYTES` before upload or queueing. Persisted queue and thumbnail files are validated before retry or serving.

Captured images, queue entries, dead letters, and cached preview thumbnails are sensitive device data. Successful captures remove their temporary local JPEG after upload; runtime cleanup also covers cached preview thumbnails. Standalone S3 public URL generation depends on operator-controlled bucket and proxy policy, so public object URLs are an explicit standalone storage choice rather than a backend-paired privacy boundary.

## Observability

Structured logging is always enabled. Optional components are configured by env:

- Loki-compatible log shipping through the `observability-ship` Compose profile
- OTLP tracing through `OTEL_ENABLED=true` and `OTEL_EXPORTER_OTLP_ENDPOINT`
- local telemetry collection exposed through protected system routes

The log inventory, retention defaults, and access expectations are documented in `INSTALL.md`.

Tracing setup lives in `app/observability/tracing.py`; logging and request-id context live in `app/observability/logging.py` and `app/core/middleware.py`.

## Shared Contract Package

`relab_rpi_cam_models` contains the backend-to-plugin device DTOs. It validates pairing bootstrap payloads, local access bootstrap data, upload acknowledgements, relay command wire shape, and the shared relay command allowlist. Backend and plugin runtime code enforce that shared policy; frontend code consumes backend OpenAPI rather than importing device DTOs directly.
