# Contributing

Development guide for the RPI Camera Plugin.

## Setup

### Local Development

Prepare your development environment:

```sh
./scripts/local_setup.sh --dev
```

This installs dependencies, configures your venv, and sets up pre-commit hooks.

### Running the Dev Server

Start with hot reload:

```sh
just dev
```

The API will be available at `http://localhost:8018/docs`.

## Project Structure

The app uses a **feature-first** layout: each domain is a self-contained package
with its HTTP router, schemas, dependencies, exceptions, and services colocated.
Cross-cutting infrastructure (relay, backend client, upload queue, image sinks,
media pipeline) lives as peer packages at `app/` root.

```
app/
  main.py                # FastAPI app creation + wiring
  router.py              # Top-level HTTP router aggregator (public vs authed)
  device_jwt.py          # Shared device-assertion primitive

  core/                  # Runtime + config + lifespan/middleware wiring
    runtime.py, runtime_state.py, runtime_context.py
    config.py, settings.py, bootstrap.py
    lifespan.py, middleware.py, templates_config.py

  # Features (own a router.py that exports `public_router` and `router`)
  camera/                # Camera controls, captures, HLS preview, streaming
    router.py, routers/{controls,captures,hls,stream}.py
    schemas.py, dependencies.py, exceptions.py
    services/{manager,backend,picamera2_backend,hardware_protocols,hardware_stubs}.py
  pairing/               # Device pairing flow + setup UI + local-access + local-key
    router.py, routers/{setup,local_access,local_key}.py
    services/{service,client}.py
  auth/                  # Session auth + request-auth dependency
    router.py, dependencies.py
  system/                # /metrics + /telemetry HTTP surfaces
    router.py, routers/{metrics,telemetry}.py
  frontend/              # Landing HTML page
    router.py

  # Infrastructure (cross-cutting — no HTTP routers)
  backend/               # Backend HTTP client + factory + contract adapters
  relay/                 # Outbound WebSocket relay service + observable state
  media/                 # MediaMTX client, preview pipeline, stream helpers
  upload/                # Persistent upload queue
  image_sinks/           # Backend / S3 image sink implementations

  observability/         # Logging, tracing (OTel), telemetry collector
  utils/                 # Generic helpers (files, network, task orchestration)
  workers/               # Process-wide background tasks (preview sleeper, thermal, etc.)
  static/, templates/    # Web assets

relab_rpi_cam_models/
  src/                   # Shared device-seam DTOs (separately published PyPI package)

tests/
  unit/                  # Mirrors app/ domain layout (camera/, pairing/, …, core/)
  integration/           # ASGI app, routes, and lifespan behavior (flat)
  support/               # Shared fakes and fixtures

scripts/
  local_setup.sh                  # Local development setup
  generate_compose_override.py    # Docker device mapping for the compose `app` service
```

## Testing

### Run Tests

```sh
uv run pytest tests
```

Or via `just`:

```sh
just test
just test-unit
just test-integration
just test-slowest
```

### Coverage

Check test coverage:

```sh
uv run pytest --cov=app tests
```

The project aims for >80% coverage. CI will fail if coverage drops.

## Code Quality

Pre-commit hooks automatically run:

- **Linting** — `ruff check` and `ruff format`
- **Type checking** — `ty`

Hooks run before every commit. To manually check:

```sh
pre-commit run --all-files
```

Recommended local commands:

```sh
just lint
just typecheck
just test
just test-slowest
just check
```

### Test suite policy

The suite is intentionally split into two main layers:

- `tests/unit/`: pure function/service tests and small collaborators
- `tests/integration/`: ASGI app, route, and lifespan behavior

Custom pytest markers mirror that split:

- `@pytest.mark.unit`
- `@pytest.mark.integration`
- `@pytest.mark.slow` for intentionally longer worker/lifecycle tests

Prefer these patterns when adding tests:

- use the shared runtime/app fixtures from `tests/conftest.py`
- use typed helpers from `tests/support/` for recurring fakes
- assert behavior and public contracts before asserting internal call choreography
- patch private module internals only when there is no stable seam to target

When cleaning up old tests:

- delete tests whose only purpose was covering removed implementation details
- keep or replace tests that still protect externally meaningful behavior
- avoid snapshot-style broad response dumps when explicit assertions are clearer

## Common Tasks

### Add a New Endpoint

1. Find or create the owning feature folder (e.g. `app/camera/`, `app/pairing/`)
1. Add a sub-router under its `routers/` dir and register it in the feature's `router.py`
1. Keep HTTP translation in the router; put orchestration in the feature's `services/`
1. Attach schemas to `schemas.py` and feature-specific errors to `exceptions.py`
1. Wire dependencies via runtime-aware helpers rather than importing process globals
1. Mirror the test placement under `tests/unit/<feature>/`

### Update Camera Logic

- Camera backend contract: `app/camera/services/backend.py`
- Camera orchestration: `app/camera/services/manager.py`
- Picamera2 implementation: `app/camera/services/picamera2_backend.py`
- Runtime-owned service wiring: `app/core/runtime.py`
- Shared DTOs: `relab_rpi_cam_models/src/relab_rpi_cam_models/`
- Tests: `tests/unit/camera/` and `tests/integration/`

### Update Models

Shared cross-repo contract DTOs live in the `relab_rpi_cam_models` package. Keep runtime logic in the plugin repo. After contract changes:

1. Update version in `relab_rpi_cam_models/pyproject.toml`
1. Rebuild the main project's lock file: `uv lock --upgrade relab-rpi-cam-models`

The workspace source override is for local co-development only. Treat the
published `relab-rpi-cam-models` package version as the actual cross-repo
contract baseline.

## Before Submitting a PR

1. **Run tests**: `uv run pytest tests`
1. **Check coverage**: Aim for >80%
1. **Run pre-commit**: `pre-commit run --all-files`
1. **Update docs**: If adding features, update relevant docs
1. **Test on hardware**: If you modified camera logic, test on an actual Pi

## Architecture Notes

**Runtime container** (`app/core/runtime.py::AppRuntime`) owns all long-lived services: camera manager, relay service + state, pairing service + state, preview pipeline + sleeper + thumbnail worker, thermal governor, upload queue worker, observability handle, and managed background tasks. Attach new long-lived services here instead of adding module-level singletons.

**Config vs runtime state.** `Settings` (env-backed, static) holds operator config; `RuntimeState` holds live mutable relay/local-auth/derived-auth state. If a value changes while the app is running, it lives on the runtime side.

**Orchestration entrypoints.** `PairingService` and `RelayService` are the only production entrypoints for pairing and relay flows.

**Contracts.** Backend OpenAPI is the public frontend contract. `relab_rpi_cam_models` is the private backend↔plugin device seam — frontend code never imports from it, and new cross-repo payloads go into the shared package first.

**Connection.** The plugin opens an outbound WebSocket relay ([app/relay/service.py](app/relay/service.py)) to the backend. No public IP or port forwarding.

**Camera capture.** Real hardware: `libcamera` via `picamera2`. Tests: synthetic image generation.

**Streaming.** YouTube RTMP only. The Pi publishes into MediaMTX locally; MediaMTX handles the egress to YouTube.

## Debugging

### Logs

View Docker logs:

```sh
docker compose logs -f app
```

If you start the optional `observability-ship` profile, Alloy ships the app's structured file logs to the external Loki-compatible endpoint configured by `LOKI_PUSH_URL`. Local Loki/Grafana is not bundled with this plugin.

View direct server logs:

```sh
just dev
```

### Interactive Testing

- **Swagger UI**: `http://localhost:8018/docs`
- **Setup page**: `http://localhost:8018/setup`

### Environment Variables

Configuration precedence is:

1. environment variables
1. relay credentials file (`~/.config/relab/relay_credentials.json`) for generated/runtime secrets
1. generated defaults on first boot where applicable

Key debugging settings:

- `DEBUG=true` — Enable debug logging
- `CAMERA_DEVICE_NUM=0` — Switch camera device (if multi-camera setup)
- `OTEL_ENABLED=true` and `OTEL_EXPORTER_OTLP_ENDPOINT=...` — enable trace export

## Release Process

Plugin application releases and `relab_rpi_cam_models` releases are versioned independently.

### Plugin App

The plugin app release remains fully automated via `commitizen` and GitHub Actions.

1. Write commits following [Conventional Commits](https://www.conventionalcommits.org/) — `fix:` bumps patch, `feat:` bumps minor, `feat!:` / `BREAKING CHANGE:` bumps major
1. Merge to `main` — CI runs lint, tests, and dependency audit
1. On CI success, the release workflow automatically:
   - Bumps the version in `pyproject.toml` and `app/__version__.py`
   - Updates `CHANGELOG.md`
   - Pushes a `vX.Y.Z` tag
   - Creates a GitHub release with auto-generated notes

If no commits since the last tag warrant a bump, the plugin release step skips silently.

### `relab_rpi_cam_models`

The contract package publishes independently to PyPI.

1. Update `relab_rpi_cam_models/pyproject.toml` to the package version you want to publish
1. Rebuild the workspace lock file: `uv lock --upgrade relab-rpi-cam-models`
1. Merge the package changes to `main`
1. Create and push a tag named `relab-rpi-cam-models-vX.Y.Z`

The publish workflow verifies that the tag version matches `relab_rpi_cam_models/pyproject.toml`, reruns package-focused checks, builds the distributions, and publishes them to PyPI via GitHub trusted publishing.

## Questions?

- Check [INSTALL.md](INSTALL.md) for setup issues
- See [README.md](README.md) for project overview
- Review existing code and tests for patterns
