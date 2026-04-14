"""Tests for the ImageSink factory."""

from __future__ import annotations

import pytest

from app.api.services.image_sinks.backend_sink import BackendPushSink
from app.api.services.image_sinks.factory import ImageSinkConfigError, get_image_sink
from app.api.services.image_sinks.s3_sink import S3CompatibleSink
from app.core.config import settings


def _clear_s3_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset S3-related settings so auto-inference behaves predictably."""
    for key in (
        "s3_endpoint_url",
        "s3_bucket",
        "s3_access_key_id",
        "s3_secret_access_key",
    ):
        monkeypatch.setattr(settings, key, "")


def _set_s3_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Populate settings with a plausible S3 credential bundle."""
    monkeypatch.setattr(settings, "s3_endpoint_url", "http://minio.local:9000")
    monkeypatch.setattr(settings, "s3_bucket", "rpi-cam")
    monkeypatch.setattr(settings, "s3_access_key_id", "ak")
    monkeypatch.setattr(settings, "s3_secret_access_key", "sk")
    monkeypatch.setattr(settings, "s3_region", "us-east-1")
    monkeypatch.setattr(settings, "s3_public_url_template", "{endpoint}/{bucket}/{key}")


class TestExplicitBackend:
    """``image_sink=backend`` returns a ``BackendPushSink`` unconditionally."""

    def test_explicit_backend_returns_backend_sink(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If the image_sink setting is set to 'backend', return a BackendPushSink."""
        monkeypatch.setattr(settings, "image_sink", "backend")
        result = get_image_sink(settings)
        assert isinstance(result, BackendPushSink)


class TestExplicitS3:
    """`image_sink=s3` validates the S3 credential bundle and hard-errors on missing fields."""

    def test_explicit_s3_with_full_config_builds_sink(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If all required S3 config fields are present, the factory should build an S3CompatibleSink without error."""
        monkeypatch.setattr(settings, "image_sink", "s3")
        _set_s3_config(monkeypatch)

        result = get_image_sink(settings)
        assert isinstance(result, S3CompatibleSink)

    def test_explicit_s3_missing_bucket_hard_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If the bucket name is missing from settings, the factory should raise an ImageSinkConfigError."""
        monkeypatch.setattr(settings, "image_sink", "s3")
        _set_s3_config(monkeypatch)
        monkeypatch.setattr(settings, "s3_bucket", "")

        with pytest.raises(ImageSinkConfigError, match="S3_BUCKET"):
            get_image_sink(settings)

    def test_explicit_s3_missing_access_key_hard_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If the access key ID is missing from settings, the factory should raise an ImageSinkConfigError."""
        monkeypatch.setattr(settings, "image_sink", "s3")
        _set_s3_config(monkeypatch)
        monkeypatch.setattr(settings, "s3_access_key_id", "")

        with pytest.raises(ImageSinkConfigError, match="S3_ACCESS_KEY_ID"):
            get_image_sink(settings)


class TestAutoInference:
    """``image_sink=auto`` (default) picks based on what's configured."""

    def test_auto_with_only_pairing_backend_url_picks_backend(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Auto inference should pick the backend push sink if only the pairing backend URL is configured."""
        monkeypatch.setattr(settings, "image_sink", "auto")
        _clear_s3_config(monkeypatch)
        monkeypatch.setattr(settings, "pairing_backend_url", "https://backend.example")

        result = get_image_sink(settings)
        assert isinstance(result, BackendPushSink)

    def test_auto_with_full_s3_config_picks_s3(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Auto inference should pick S3 when the full credential bundle is present."""
        monkeypatch.setattr(settings, "image_sink", "auto")
        _set_s3_config(monkeypatch)
        monkeypatch.setattr(settings, "pairing_backend_url", "")

        result = get_image_sink(settings)
        assert isinstance(result, S3CompatibleSink)

    def test_auto_with_nothing_configured_hard_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If neither S3 nor pairing backend config is present, auto inference should raise an error."""
        monkeypatch.setattr(settings, "image_sink", "auto")
        _clear_s3_config(monkeypatch)
        monkeypatch.setattr(settings, "pairing_backend_url", "")

        with pytest.raises(ImageSinkConfigError, match="No image sink configured"):
            get_image_sink(settings)

    def test_auto_prefers_s3_when_both_are_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If both pairing + S3 are configured, S3 wins — the user went out of their way to set it."""
        monkeypatch.setattr(settings, "image_sink", "auto")
        _set_s3_config(monkeypatch)
        monkeypatch.setattr(settings, "pairing_backend_url", "https://backend.example")

        result = get_image_sink(settings)
        assert isinstance(result, S3CompatibleSink)


class TestUnknownSinkName:
    """Unknown sink names fail fast at startup."""

    def test_unknown_sink_name_hard_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If the image_sink setting is set to an unrecognized value, raise an error."""
        monkeypatch.setattr(settings, "image_sink", "garbage")
        with pytest.raises(ImageSinkConfigError, match="Unknown image_sink"):
            get_image_sink(settings)
