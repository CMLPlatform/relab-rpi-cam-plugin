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

# Print the local direct-connection API key from the local key endpoint.
show-key:
    @python3 -c 'import sys; from urllib.request import urlopen; sys.stdout.write(urlopen("http://127.0.0.1:8018/local-key", timeout=2).read().decode("utf-8", "replace").strip() + "\n")'
