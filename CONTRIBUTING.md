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

- Camera interface: `app/utils/camera.py`
- Stream handling: `relab_rpi_cam_models/src/relab_rpi_cam_models/stream.py`
- Tests: `tests/unit/test_*.py`

### Update Models

Shared data models live in the `relab_rpi_cam_models` package. After changes:

1. Update version in `relab_rpi_cam_models/pyproject.toml`
1. Rebuild the main project's lock file: `uv lock --upgrade relab-rpi-cam-models`

## Before Submitting a PR

1. **Run tests**: `uv run pytest tests/`
1. **Check coverage**: Aim for >80%
1. **Run pre-commit**: `pre-commit run --all-files`
1. **Update docs**: If adding features, update relevant docs
1. **Test on hardware**: If you modified camera logic, test on an actual Pi

## Architecture Notes

### Connection Modes

- **WebSocket Relay** (`app/utils/relay.py`) — Pi initiates outbound connection, backend sends commands through tunnel
- **Direct HTTP** (`app/api/`) — Backend makes HTTP requests directly to Pi API

Both modes are supported simultaneously via environment configuration.

### Camera Capture

- Real camera: Uses `libcamera` via `rpicam` tools
- Mock mode (testing): Uses synthetic image generation

### Relay Protocol

The WebSocket relay uses a simple request-response protocol. See `relay_credentials.json` format in [INSTALL.md](INSTALL.md#option-b-manual-websocket-setup).

## Debugging

### Logs

View Docker logs:

```sh
docker compose logs -f rpi-cam-plugin
```

View direct server logs:

```sh
uv run fastapi dev app/main.py
```

### Interactive Testing

- **Swagger UI**: `http://localhost:8018/docs`
- **Setup page**: `http://localhost:8018/setup`
- **Raw stream**: `http://localhost:8018/stream/watch`

### Environment Variables

All configuration is in `.env`. Key debugging settings:

- `DEBUG=true` — Enable debug logging
- `CAMERA_DEVICE_NUM=0` — Switch camera device (if multi-camera setup)
- `LOG_LEVEL=debug` — More verbose logs

## Release Process

Versioning is handled automatically by `commitizen`. The process is:

1. Make commits following [Conventional Commits](https://www.conventionalcommits.org/) format
1. Run `cz bump` to update version and generate changelog
1. Push the version tag: `git push origin --tags`
1. CI will build and package the release

## Questions?

- Check [INSTALL.md](INSTALL.md) for setup issues
- See [README.md](README.md) for project overview
- Review existing code and tests for patterns
