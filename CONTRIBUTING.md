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
uv run fastapi dev app/main.py --port 8018
```

The API will be available at `http://localhost:8018/docs`.

## Project Structure

```
app/
  main.py              # FastAPI application entry point
  api/routers/         # API endpoint definitions
  utils/               # Utility modules (camera, files, relay, etc.)
  static/              # CSS and frontend assets
  templates/           # HTML templates (setup page)
relab_rpi_cam_models/
  src/                 # Shared data models (PyPI package)
tests/
  unit/                # Unit tests
  integration/         # Integration tests
scripts/
  local_setup.sh       # Local development setup
  generate_compose_override.py  # Docker device mapping
```

## Testing

### Run Tests

```sh
uv run pytest tests/
```

### Coverage

Check test coverage:

```sh
uv run pytest --cov=app tests/
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

## Common Tasks

### Add a New Endpoint

1. Create a new route file in `app/api/routers/`

1. Import and include it in `app/main.py`:

   ```python
   from app.api.routers import your_router
   app.include_router(your_router.router)
   ```

1. Add tests in `tests/unit/`

### Update Camera Logic

- Camera backend interface: `app/api/services/camera_backend.py`
- Camera manager orchestration: `app/api/services/camera_manager.py`
- Runtime stream state: `app/api/services/stream_state.py`
- Shared stream DTOs: `relab_rpi_cam_models/src/relab_rpi_cam_models/stream.py`
- Tests: `tests/unit/test_*.py`

### Update Models

Shared cross-repo contract DTOs live in the `relab_rpi_cam_models` package. Keep runtime logic in the plugin repo. After contract changes:

1. Update version in `relab_rpi_cam_models/pyproject.toml`
1. Rebuild the main project's lock file: `uv lock --upgrade relab-rpi-cam-models`

## Before Submitting a PR

1. **Run tests**: `uv run pytest tests/`
1. **Check coverage**: Aim for >80%
1. **Run pre-commit**: `pre-commit run --all-files`
1. **Update docs**: If adding features, update relevant docs
1. **Test on hardware**: If you modified camera logic, test on an actual Pi

## Architecture Notes

### Connection

The plugin connects to the RELab backend via **WebSocket relay** (`app/utils/relay.py`). The Pi initiates an outbound WebSocket connection; the backend sends commands through this tunnel. No public IP or port forwarding is needed.

See the relay credential format in [INSTALL.md](INSTALL.md#option-b-manual-websocket-setup).

### Camera Capture

- Real camera: Uses `libcamera` via `picamera2`
- Mock mode (testing): Uses synthetic image generation

### Streaming

YouTube RTMP is the only supported streaming mode. The Pi sends HLS segments to YouTube's ingestion API via ffmpeg. Local HLS preview and MJPEG streaming have been removed (see ADR-001).

## Debugging

### Logs

View Docker logs:

```sh
docker compose logs -f app
```

If you started both optional observability profiles, Alloy ships logs to Loki and Grafana is available on `http://localhost:3000`; Alloy can be inspected on `http://localhost:12345`. If you start only `observability-ship`, Alloy still runs but Grafana and Loki are expected to live elsewhere.

View direct server logs:

```sh
uv run fastapi dev app/main.py
```

### Interactive Testing

- **Swagger UI**: `http://localhost:8018/docs`
- **Setup page**: `http://localhost:8018/setup`

### Environment Variables

All configuration is in `.env`. Key debugging settings:

- `DEBUG=true` — Enable debug logging
- `CAMERA_DEVICE_NUM=0` — Switch camera device (if multi-camera setup)
- `LOG_LEVEL=debug` — More verbose logs

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
