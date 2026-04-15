set dotenv-load := true

default:
    @just --list

install:
    uv sync --all-groups

dev:
    uv run fastapi dev

test:
    uv run pytest --cov=app --cov=relab_rpi_cam_models --cov-report=term-missing

lint:
    uv run ruff check --no-fix .
    uv run ty check .

format:
    uv run ruff check --fix .
    uv run ruff format .

pre-commit:
    uv run pre-commit run --all-files

check: format lint test

audit:
    uv audit --preview-features audit --locked --no-dev

build:
    uv build

clean:
    rm -rf dist/ .venv/ .pytest_cache/ __pycache__/

# Print the local direct-connection API key (SSH / headless access).
# The key is auto-generated on startup and stored in the credentials file.
show-key:
    @python3 -c "
import json, pathlib, sys
f = pathlib.Path.home() / '.config/relab/relay_credentials.json'
if not f.exists():
    print('No credentials file found — start the camera once to generate a key.')
    sys.exit(1)
d = json.loads(f.read_text())
key = d.get('local_api_key', '')
if key:
    print(key)
else:
    print('No local_api_key in credentials file yet — restart the camera.')
    sys.exit(1)
"
