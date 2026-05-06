"""Tests for committed OpenAPI schema export tooling."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from scripts.export_openapi import export_openapi_schema, schema_is_current

if TYPE_CHECKING:
    from pathlib import Path

SCHEMA_TITLE = "Raspberry Pi Camera API"
CAMERA_PATH = "/camera"


def test_export_openapi_schema_writes_deterministic_schema(tmp_path: Path) -> None:
    """The export helper should write a stable JSON OpenAPI artifact."""
    output_path = tmp_path / "openapi.rpi-cam.json"

    export_openapi_schema(output_path)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["openapi"].startswith("3.")
    assert payload["info"]["title"] == SCHEMA_TITLE
    assert CAMERA_PATH in payload["paths"]
    assert output_path.read_text(encoding="utf-8").endswith("\n")


def test_schema_is_current_detects_stale_file(tmp_path: Path) -> None:
    """The check helper should fail cleanly when a committed schema is stale."""
    output_path = tmp_path / "openapi.rpi-cam.json"
    output_path.write_text('{"stale": true}\n', encoding="utf-8")

    assert not schema_is_current(output_path)

    export_openapi_schema(output_path)

    assert schema_is_current(output_path)
