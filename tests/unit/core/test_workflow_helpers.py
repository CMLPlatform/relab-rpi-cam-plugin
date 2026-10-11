"""Tests for workflow helper scripts."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING

from scripts import check_models_release, validate_models_package_tag

if TYPE_CHECKING:
    import pytest

VALIDATED_MESSAGE = "Validated relab-rpi-cam-models-v1.2.3 against relab_rpi_cam_models 1.2.3"
UNEXPECTED_TAG_MESSAGE = "Unexpected tag name: bad-tag"


def test_validate_models_package_tag_accepts_matching_tag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Matching tag and package version should validate successfully."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    args = argparse.Namespace(
        tag_name="relab-rpi-cam-models-v1.2.3",
        pyproject=pyproject,
        prefix="relab-rpi-cam-models-v",
    )
    monkeypatch.setattr(validate_models_package_tag, "parse_args", lambda: args)

    assert validate_models_package_tag.main() == 0
    assert VALIDATED_MESSAGE in capsys.readouterr().out


def test_validate_models_package_tag_rejects_wrong_prefix(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Unexpected tag prefixes should fail fast."""
    args = argparse.Namespace(
        tag_name="bad-tag",
        pyproject=Path("relab_rpi_cam_models/pyproject.toml"),
        prefix="relab-rpi-cam-models-v",
    )
    monkeypatch.setattr(validate_models_package_tag, "parse_args", lambda: args)

    assert validate_models_package_tag.main() == 1
    assert UNEXPECTED_TAG_MESSAGE in capsys.readouterr().err


def test_models_release_drift_flags_unpublished_untagged_version() -> None:
    """A workspace version missing from PyPI with no release tag is drift."""
    problem = check_models_release.find_drift("0.5.1", {"0.5.0"}, tag_exists=False)

    assert problem is not None
    assert "0.5.1" in problem


def test_models_release_drift_accepts_published_or_tagged_version() -> None:
    """A published version, or one whose tag is pushed and publishing, is in step."""
    assert check_models_release.find_drift("0.5.0", {"0.5.0"}, tag_exists=False) is None
    assert check_models_release.find_drift("0.5.1", {"0.5.0"}, tag_exists=True) is None
