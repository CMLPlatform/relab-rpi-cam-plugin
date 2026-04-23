# Architecture

A FastAPI app running on a Raspberry Pi that captures images and video, streams a
low-res preview over a WebSocket relay, and uploads originals either to the ReLab
backend or to S3-compatible storage. The RPi has no inbound network access, so all
commands arrive over an outbound WebSocket.

## Request flow

```
Browser / ReLab backend
        │
        ▼
  (outbound WS)              (HTTP, LAN / loopback)
        │                            │
        ▼                            ▼
  RelayService ──► FastAPI (app/main.py, app/router.py)
                            │
                            ▼
               feature routers: camera / pairing / system / media / auth
                            │
                            ▼
               feature services (app/<feature>/services/)
                            │
                            ▼
                     AppRuntime container
                            │
                 ┌──────────┼──────────┐
                 ▼          ▼          ▼
           CameraManager  ImageSink  workers (below)
```

Feature modules own their own routers, services, and schemas. There is no
centralized `api/` package — each module under [app/](app/) is self-contained.

## AppRuntime

[app/core/runtime.py](app/core/runtime.py) defines `AppRuntime`, a dataclass that
holds every long-lived singleton (camera manager, preview pipeline, relay service,
pairing service, upload queue worker, observability handle) plus the set of
managed background tasks. One `AppRuntime` exists per process and is stored via
`set_active_runtime` for code paths that cannot easily receive it as a parameter
(e.g. pairing code that runs before any request).

## Lifespan

[app/core/bootstrap.py](app/core/bootstrap.py) + the FastAPI lifespan:

1. Load `.env` settings ([app/core/settings.py](app/core/settings.py)).
1. Apply any persisted relay credentials from
   [app/pairing/services/credentials.py](app/pairing/services/credentials.py).
1. Ensure a local API key exists (used for direct-Ethernet access).
1. Construct `AppRuntime` and call `set_active_runtime`.
1. Start background workers and the relay service as managed tasks.
1. On shutdown, cancel every task in `background_tasks` / `recurring_tasks` and
   close the camera / relay cleanly.

## Background workers

Owned and lifecycled by `AppRuntime`:

- **ThermalGovernor** ([app/workers/thermal_governor.py](app/workers/thermal_governor.py)) —
  reads SoC temperature, throttles or pauses streaming if the Pi is overheating.
- **PreviewSleeper** ([app/workers/preview_sleeper.py](app/workers/preview_sleeper.py)) —
  stops the lores preview encoder after `preview_hibernate_after_s` of relay
  idleness; restarts it on the next command.
- **PreviewThumbnailWorker** ([app/workers/preview_thumbnail.py](app/workers/preview_thumbnail.py)) —
  keeps the setup page thumbnail fresh while the preview is active.
- **UploadQueueWorker** ([app/upload/queue.py](app/upload/queue.py)) — drains the
  file-backed retry queue for captures whose synchronous upload failed; entries
  exhaust a 5-step exponential backoff before being dead-lettered to
  `data/queue/dead/`.

## Pairing

If the device boots without relay credentials and `PAIRING_BACKEND_URL` is set,
it enters pairing mode ([app/pairing/services/service.py](app/pairing/services/service.py)):
generate a short code, register it, display it on `/setup`, poll until claimed,
persist the returned credentials via
[credentials.py](app/pairing/services/credentials.py), then hand control off to
the relay service.

## Relay

[app/relay/service.py](app/relay/service.py) maintains a single outbound
WebSocket to the backend with exponential reconnect. Inbound commands are
dispatched to feature services under a concurrency cap
(`RELAY_MAX_CONCURRENT_COMMANDS`). Authentication uses a signed device-assertion
JWT built from the private key written during pairing.

## Image sinks

`ImageSink` ([app/image_sinks/base.py](app/image_sinks/base.py)) abstracts
persistence: `BackendSink` POSTs to the ReLab backend, `S3Sink` uses
`aioboto3`. The factory at [app/image_sinks/factory.py](app/image_sinks/factory.py)
picks one based on `IMAGE_SINK` (`auto` infers from config). The upload queue is
sink-agnostic — swapping sinks requires no changes to the queue or workers.

## Configuration

All configuration lives in [app/core/settings.py](app/core/settings.py) as a
flat `Settings` (pydantic-settings v2) loaded from `.env`. Runtime-mutable state
that must not live in the settings object (relay credentials, local API key) is
kept on `RuntimeState` ([app/core/runtime_state.py](app/core/runtime_state.py))
and accessed through `AppRuntime.runtime_state`.
