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

# Print the local direct-connection API key.
# Uses the running Docker container when available; otherwise falls back to host lookup.
show-key:
    @if docker compose ps -q app >/dev/null 2>&1 && [ -n "$(docker compose ps -q app 2>/dev/null)" ]; then docker compose exec -T app python3 scripts/show_key.py; else python3 scripts/show_key.py; fi
