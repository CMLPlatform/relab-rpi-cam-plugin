# Contributing

This document is for developers changing the plugin. For operator setup, see [INSTALL.md](INSTALL.md). For internal design, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Local Development

Prepare the development environment:

```sh
./scripts/local_setup.sh --dev
```

Start the API with reload:

```sh
just dev
```

The local API docs are available at `http://localhost:8018/docs`, and the setup UI is available at `http://localhost:8018/setup`.

## Common Commands

Prefer the `justfile` targets for local work:

```sh
just lint
just typecheck
just test
just test-unit
just test-integration
just test-slowest
just check
```

## Code Quality

The repository uses:

- Ruff for linting and formatting
- `ty` for type checking
- pytest for unit and integration tests
- coverage threshold enforcement in the test gate

Before opening a PR, run:

```sh
just check
```

Use `prek run --all-files` when changing hooks or repository policy files.

## Security Maintenance

Keep the runtime small and the risky paths easy to spot:

- Fix critical and high dependency, Action, and image findings within 7 days. Fix medium findings within 30 days.
- Keep `pyproject.toml`, `uv.lock`, Dockerfile and Compose image pins, pinned GitHub Actions, Renovate PRs, `uv audit`, secret scanning, and Trivy current.
- Pay extra attention to camera capture, preview and streaming, MediaMTX `runOnReady` and ffmpeg, the upload queue, the S3 sink, relay dispatch, and telemetry and log shipping.

## Test Suite Policy

The suite has two primary layers:

- `tests/unit/`: pure functions, services, small collaborators, and focused worker behavior
- `tests/integration/`: ASGI app, route, auth, middleware, and lifespan behavior

Custom markers mirror that split:

- `@pytest.mark.unit`
- `@pytest.mark.integration`
- `@pytest.mark.slow`

Prefer these patterns:

- use shared runtime/app fixtures from `tests/conftest.py`
- use typed helpers from `tests/support/`
- test externally meaningful behavior before internal call choreography
- patch private module internals only when there is no stable seam
- keep integration tests focused on route behavior and app wiring

When removing or refactoring tests, keep coverage for public behavior and delete tests that only pin removed implementation details.

## Project Layout

The app is feature-first. Each feature package owns its HTTP layer and local services. Shared infrastructure lives as peer packages at `app/` root.

Read [ARCHITECTURE.md](ARCHITECTURE.md) before changing runtime ownership, relay flow, pairing, auth, image sinks, upload queue behavior, or shared DTOs.

Key directories:

- `app/camera/` - controls, captures, HLS preview, streaming, and camera backends
- `app/pairing/` - pairing flow, setup UI, local-access, and local-key routes
- `app/auth/` - API-key/session auth and browser login/logout
- `app/core/` - settings, runtime, lifespan, middleware, bootstrap, and templates config
- `app/relay/` - outbound WebSocket relay service and state
- `app/delivery/` - backend and S3 capture persistence
- `relab_rpi_cam_models/` - shared backend-to-plugin DTO package

## Common Changes

### Add Or Change An Endpoint

1. Find the owning feature package.
1. Add or update a router under the feature's `routers/` directory.
1. Register the router in the feature's `router.py`.
1. Keep HTTP translation in the router and orchestration in services.
1. Put request/response models in the feature's `schemas.py`.
1. Mirror test placement under `tests/unit/<feature>/` or `tests/integration/`.

### Change Camera Behavior

Start with the camera manager and backend contract:

- `app/camera/services/manager.py`
- `app/camera/services/backend.py`
- `app/camera/services/picamera2_backend.py`
- `tests/unit/camera/`
- `tests/integration/test_camera.py`

Hardware-sensitive changes should be tested on an actual Pi when possible.

### Change Runtime Services

Long-lived services belong on `AppRuntime`. Avoid module-level singletons for runtime-owned state. See [ARCHITECTURE.md#runtime-container](ARCHITECTURE.md#runtime-container).

### Change Shared DTOs

Cross-repo device payloads live in `relab_rpi_cam_models`.

1. Update the DTO package.
1. Update `relab_rpi_cam_models/pyproject.toml`.
1. Refresh the lock file with `uv lock --upgrade relab-rpi-cam-models`.
1. Add focused contract tests.

## Debugging

Docker logs:

```sh
docker compose logs -f app
```

Direct dev logs:

```sh
just dev
```

Common debugging settings:

- `DEBUG=true`
- `CAMERA_DEVICE_NUM=0`
- `OTEL_ENABLED=true`
- `OTEL_EXPORTER_OTLP_ENDPOINT=...`

## PR Checklist

Before submitting:

1. Run `just check`.
1. Add or update tests for behavior changes.
1. Update the owning doc when behavior, setup, architecture, or developer workflow changes.
1. Test camera hardware changes on a Pi when practical.
1. Keep commits scoped and easy to review.

## Release Process

Plugin app releases and `relab_rpi_cam_models` releases are versioned independently.

### Plugin App

The plugin app release uses `commitizen` and GitHub Actions.

1. Use [Conventional Commits](https://www.conventionalcommits.org/).
1. Merge to `main` after checks pass.
1. The release workflow updates `pyproject.toml`, `app/__version__.py`, `CHANGELOG.md`, the Git tag, and the GitHub release.

### `relab_rpi_cam_models`

The contract package publishes independently to PyPI.

1. Update `relab_rpi_cam_models/pyproject.toml`.
1. Run `uv lock --upgrade relab-rpi-cam-models`.
1. Merge the package changes.
1. Create and push `relab-rpi-cam-models-vX.Y.Z`.

The publish workflow verifies the tag version, runs package-focused checks, builds distributions, and publishes via GitHub trusted publishing.
