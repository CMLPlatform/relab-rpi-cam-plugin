set dotenv-load := true

default:
    @just --list

install:
    uv sync --all-extras

dev:
    uv run fastapi dev

test:
    uv run pytest

lint:
    uv run ruff check .
    uv run ty check .

format:
    uv run ruff format .

build:
    uv build

clean:
    rm -rf dist/ .venv/ .pytest_cache/ __pycache__/
