"""Export the plugin OpenAPI schema to a committed JSON artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.main import app

DEFAULT_SCHEMA_PATH = Path("openapi.rpi-cam.json")


def _schema_text() -> str:
    """Return the canonical schema JSON text written to disk."""
    schema: dict[str, object] = app.openapi()
    return f"{json.dumps(schema, indent=2, sort_keys=True)}\n"


def export_openapi_schema(output_path: Path = DEFAULT_SCHEMA_PATH) -> None:
    """Write the current OpenAPI schema to ``output_path``."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_schema_text(), encoding="utf-8")


def schema_is_current(output_path: Path = DEFAULT_SCHEMA_PATH) -> bool:
    """Return whether ``output_path`` already contains the current schema."""
    if not output_path.exists():
        return False
    return output_path.read_text(encoding="utf-8") == _schema_text()


def main() -> int:
    """Command-line entry point for export and CI drift checks."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_SCHEMA_PATH,
        help=f"Schema artifact path. Defaults to {DEFAULT_SCHEMA_PATH}.",
    )
    parser.add_argument("--check", action="store_true", help="Fail if the schema artifact is stale.")
    args = parser.parse_args()

    if args.check:
        return 0 if schema_is_current(args.output) else 1

    export_openapi_schema(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
