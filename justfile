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
    uv run ruff format --check .
    uv run ty check .

workflow-lint:
    uv run pre-commit run actionlint --all-files

format:
    uv run ruff check --fix .
    uv run ruff format .

check: lint workflow-lint test

audit:
    uv audit --preview-features audit --locked --no-dev

build:
    uv build

clean:
    rm -rf dist/ .venv/ .pytest_cache/ __pycache__/
