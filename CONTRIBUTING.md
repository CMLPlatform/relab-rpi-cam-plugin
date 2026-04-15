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

```
app/
  main.py              # FastAPI app assembly and lifespan wiring
  core/runtime.py      # Runtime-owned process services and task tracking
  api/routers/         # HTTP translation layer
  api/services/        # Application services and adapters
  utils/               # Infra helpers (relay, pairing, logging, telemetry, etc.)
  static/              # CSS and frontend assets
  templates/           # HTML templates (setup page)
relab_rpi_cam_models/
  src/                 # Shared data models (PyPI package)
tests/
  unit/                # Unit tests
  integration/         # Integration tests
scripts/
  local_setup.sh       # Local development setup
  generate_compose_override.py  # Docker device mapping for the existing compose `app` service
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

1. Add or extend a router in `app/api/routers/`
1. Keep HTTP translation in the router and put orchestration in `app/api/services/`
1. Wire dependencies through runtime-aware helpers rather than importing process globals
1. Add unit or integration coverage in `tests/`

### Update Camera Logic

- Camera backend contract: `app/api/services/camera_backend.py`
- Camera orchestration: `app/api/services/camera_manager.py`
- Runtime-owned service wiring: `app/core/runtime.py`
- Shared DTOs: `relab_rpi_cam_models/src/relab_rpi_cam_models/`
- Tests: `tests/unit/` and `tests/integration/`

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

### Runtime shape

The app owns its long-lived services through `app.core.runtime.AppRuntime`.
That runtime tracks:

- the shared camera manager
- relay state and relay service
- pairing state and pairing service
- preview pipeline / sleeper
- thermal governor
- managed background tasks
- recurring maintenance tasks
- the optional observability handle

New long-lived services should be attached there rather than added as new
module-level singletons. When refactoring older code, prefer deleting hidden
globals instead of adding new compatibility shims around them.

Mutable runtime data follows the same rule:

- `Settings`: env-backed, effectively static process config
- `RuntimeState`: live mutable relay/local/auth state for the running process

If a value changes while the app is running, it should usually live on the
runtime side, not on `settings` and not in a module global.

The same ownership rule applies to orchestration code:

- `PairingService` is the only production pairing entrypoint
- `RelayService` is the only production relay entrypoint

Contract ownership follows the same boundary:

- backend OpenAPI is the only frontend/public contract
- `relab_rpi_cam_models` is the private backend<->plugin device seam
- frontend code should not import private seam DTOs directly
- new cross-repo device payloads go into the shared models package first,
  rather than being duplicated into backend/plugin by hand

### Connection

The plugin connects to the RELab backend via **WebSocket relay** (`app/utils/relay.py`).
The Pi initiates an outbound WebSocket connection; the backend sends commands
through this tunnel. No public IP or port forwarding is needed.

### Camera Capture

- Real camera: Uses `libcamera` via `picamera2`
- Mock mode (testing): Uses synthetic image generation

### Streaming

YouTube RTMP is the only supported streaming mode. The Pi publishes into
MediaMTX locally and MediaMTX handles the YouTube egress path. Local HLS
preview and MJPEG streaming have been removed.

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
