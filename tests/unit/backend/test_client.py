"""Tests for the Pi-side backend upload client."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from app.backend import client as backend_client_mod
from app.backend.client import BackendUploadError, upload_image
from app.core.runtime import AppRuntime, set_active_runtime
from app.core.settings import settings
from tests.constants import BACKEND_IMAGE_URL, SAMPLE_SERVER_IMAGE_ID

if TYPE_CHECKING:
    from collections.abc import Iterator
    from contextlib import AbstractContextManager

CAPTURE_METADATA_JSON = '{"width": 1920}'
UPLOAD_METADATA_JSON = '{"product_id": 1}'
AUTHORIZATION_HEADER = "Bearer fake.jwt.token"
DEVICE_ASSERTION_ERROR = "bad key"
NO_BACKEND_URL_LOG = "notify_self_unpair: no PAIRING_BACKEND_URL, skipping"
NO_RELAY_LOG = "notify_self_unpair: relay credentials missing, skipping"
ASSERTION_WARNING_LOG = "notify_self_unpair: could not mint device assertion"
ACKNOWLEDGED_UNPAIR_LOG = "backend acknowledged unpair"
UNEXPECTED_STATUS_LOG = "backend returned HTTP 500"
NETWORK_WARNING_LOG = "network error reaching backend"


@pytest.fixture(autouse=True)
def _relay_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide the static backend URL needed by the upload client."""
    monkeypatch.setattr(settings, "pairing_backend_url", "https://backend.example/")
    monkeypatch.setattr(backend_client_mod, "build_device_assertion", lambda: "fake.jwt.token")


@pytest.fixture(autouse=True)
def _active_runtime() -> Iterator[AppRuntime]:
    """Install relay credentials into a runtime-owned state for each test."""
    runtime = AppRuntime()
    runtime.runtime_state.set_relay_credentials(
        relay_backend_url="wss://backend.example/ws",
        relay_camera_id=str(uuid4()),
        relay_auth_scheme="device_assertion",
        relay_key_id="fake-kid",
        relay_private_key_pem="fake-pem",
    )
    set_active_runtime(runtime)
    try:
        yield runtime
    finally:
        set_active_runtime(None)


def _fake_response(status: int, body: object) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json = MagicMock(return_value=body)
    resp.text = str(body)
    return resp


def _patch_async_client(response: MagicMock) -> AbstractContextManager[MagicMock]:
    client_instance = MagicMock()
    client_instance.post = AsyncMock(return_value=response)
    client_instance.delete = AsyncMock(return_value=response)
    client_instance.__aenter__ = AsyncMock(return_value=client_instance)
    client_instance.__aexit__ = AsyncMock(return_value=None)
    return patch.object(backend_client_mod.httpx, "AsyncClient", return_value=client_instance)


class TestUploadImage:
    """Tests for upload_image happy and failure paths."""

    async def test_happy_path_returns_uploaded_info(self) -> None:
        """A 200 JSON response with image_id+image_url should populate UploadedImageInfo."""
        response = _fake_response(
            200,
            {"image_id": SAMPLE_SERVER_IMAGE_ID, "image_url": BACKEND_IMAGE_URL},
        )
        with _patch_async_client(response) as patched_client:
            result = await upload_image(
                image_bytes=b"\xff\xd8fake-jpg",
                filename="test.jpg",
                capture_metadata={"width": 1920},
                upload_metadata={"product_id": 1},
            )

        assert result.image_id == SAMPLE_SERVER_IMAGE_ID
        assert str(result.image_url) == BACKEND_IMAGE_URL
        client_instance = patched_client.return_value
        client_instance.post.assert_awaited_once()
        _, kwargs = client_instance.post.await_args
        assert kwargs["files"]["file"] == ("test.jpg", b"\xff\xd8fake-jpg", "image/jpeg")
        assert kwargs["data"]["capture_metadata"] == CAPTURE_METADATA_JSON
        assert kwargs["data"]["upload_metadata"] == UPLOAD_METADATA_JSON
        assert kwargs["headers"]["Authorization"] == AUTHORIZATION_HEADER

    async def test_relative_image_url_is_prefixed_with_base_url(self) -> None:
        """A relative image_url from the backend should be resolved against the pairing base URL."""
        response = _fake_response(
            200,
            {"image_id": SAMPLE_SERVER_IMAGE_ID, "image_url": "/images/abc.jpg"},
        )
        with _patch_async_client(response):
            result = await upload_image(
                image_bytes=b"\xff\xd8fake-jpg",
                filename="test.jpg",
                capture_metadata={"width": 1920},
                upload_metadata={"product_id": 1},
            )

        assert result.image_id == SAMPLE_SERVER_IMAGE_ID
        assert str(result.image_url) == BACKEND_IMAGE_URL

    async def test_http_error_wrapped_in_backend_upload_error(self) -> None:
        """An httpx transport error should surface as BackendUploadError."""
        client_instance = MagicMock()
        client_instance.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=None)

        with (
            patch.object(backend_client_mod.httpx, "AsyncClient", return_value=client_instance),
            pytest.raises(BackendUploadError, match="Network error"),
        ):
            await upload_image(
                image_bytes=b"\xff\xd8",
                filename="test.jpg",
                capture_metadata={},
                upload_metadata={},
            )

    async def test_backend_4xx_raises(self) -> None:
        """A 4xx response should raise BackendUploadError with status code context."""
        response = _fake_response(401, {"detail": "bad jwt"})
        response.text = "bad jwt"
        with _patch_async_client(response), pytest.raises(BackendUploadError, match="HTTP 401"):
            await upload_image(
                image_bytes=b"\xff\xd8",
                filename="test.jpg",
                capture_metadata={},
                upload_metadata={},
            )

    async def test_malformed_json_raises(self) -> None:
        """A 200 response that can't be parsed as JSON should raise."""
        response = MagicMock()
        response.status_code = 200
        response.json = MagicMock(side_effect=ValueError("not json"))
        response.text = "not json"
        with _patch_async_client(response), pytest.raises(BackendUploadError, match="not JSON"):
            await upload_image(
                image_bytes=b"\xff\xd8",
                filename="test.jpg",
                capture_metadata={},
                upload_metadata={},
            )

    async def test_missing_fields_raises(self) -> None:
        """A 200 response that doesn't include image_id+image_url must be rejected."""
        response = _fake_response(200, {"image_id": "abc"})
        with _patch_async_client(response), pytest.raises(BackendUploadError, match="missing fields"):
            await upload_image(
                image_bytes=b"\xff\xd8",
                filename="test.jpg",
                capture_metadata={},
                upload_metadata={},
            )

    async def test_requires_pairing_backend_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Uploading without a configured pairing backend must fail cleanly."""
        monkeypatch.setattr(settings, "pairing_backend_url", "")
        with pytest.raises(BackendUploadError, match="PAIRING_BACKEND_URL"):
            await upload_image(
                image_bytes=b"\xff\xd8",
                filename="test.jpg",
                capture_metadata={},
                upload_metadata={},
            )

    @pytest.mark.usefixtures("_active_runtime")
    async def test_requires_relay_enabled(self) -> None:
        """Uploading before the device is paired must fail cleanly.

        Clearing runtime relay credentials should make the upload fail fast.
        """
        runtime = backend_client_mod.get_active_runtime()
        runtime.runtime_state.clear_relay_credentials()
        with pytest.raises(BackendUploadError, match="unpaired"):
            await upload_image(
                image_bytes=b"\xff\xd8",
                filename="test.jpg",
                capture_metadata={},
                upload_metadata={},
            )

    async def test_device_assertion_mint_failures_are_wrapped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """JWT minting errors should surface as BackendUploadError before networking starts."""
        monkeypatch.setattr(
            backend_client_mod,
            "build_device_assertion",
            lambda: (_ for _ in ()).throw(ValueError(DEVICE_ASSERTION_ERROR)),
        )

        with pytest.raises(BackendUploadError, match="Failed to mint device assertion"):
            await upload_image(
                image_bytes=b"\xff\xd8",
                filename="test.jpg",
                capture_metadata={},
                upload_metadata={},
            )


class TestNotifySelfUnpair:
    """Tests for best-effort backend self-unpair calls."""

    async def test_skips_when_pairing_backend_url_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Without the backend base URL, self-unpair should no-op with a debug log."""
        monkeypatch.setattr(settings, "pairing_backend_url", "")

        with caplog.at_level(logging.DEBUG):
            await backend_client_mod.notify_self_unpair()

        assert NO_BACKEND_URL_LOG in caplog.text

    async def test_skips_when_relay_credentials_missing(self, caplog: pytest.LogCaptureFixture) -> None:
        """Without relay credentials, there is nothing meaningful to unpair."""
        runtime = backend_client_mod.get_active_runtime()
        runtime.runtime_state.clear_relay_credentials()

        with caplog.at_level(logging.DEBUG):
            await backend_client_mod.notify_self_unpair()

        assert NO_RELAY_LOG in caplog.text

    async def test_skips_when_device_assertion_cannot_be_built(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """JWT minting errors should be logged and swallowed."""
        monkeypatch.setattr(
            backend_client_mod,
            "build_device_assertion",
            lambda: (_ for _ in ()).throw(TypeError("missing key")),
        )

        with caplog.at_level(logging.WARNING):
            await backend_client_mod.notify_self_unpair()

        assert ASSERTION_WARNING_LOG in caplog.text

    @pytest.mark.parametrize("status_code", [200, 204, 404])
    async def test_accepts_backend_acknowledgement_statuses(
        self,
        status_code: int,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """200/204/404 are all valid best-effort acknowledgements for self-unpair."""
        response = _fake_response(status_code, {})

        with _patch_async_client(response) as patched_client, caplog.at_level(logging.INFO):
            await backend_client_mod.notify_self_unpair()

        patched_client.return_value.delete.assert_awaited_once()
        assert ACKNOWLEDGED_UNPAIR_LOG in caplog.text

    async def test_warns_when_backend_returns_unexpected_status(self, caplog: pytest.LogCaptureFixture) -> None:
        """Unexpected backend statuses should be logged as warnings, not raised."""
        response = _fake_response(500, {})

        with _patch_async_client(response), caplog.at_level(logging.WARNING):
            await backend_client_mod.notify_self_unpair()

        assert UNEXPECTED_STATUS_LOG in caplog.text

    async def test_warns_when_backend_delete_has_network_error(self, caplog: pytest.LogCaptureFixture) -> None:
        """Transport failures should also stay best-effort and warning-only."""
        client_instance = MagicMock()
        client_instance.delete = AsyncMock(side_effect=httpx.ConnectError("refused"))
        client_instance.__aenter__ = AsyncMock(return_value=client_instance)
        client_instance.__aexit__ = AsyncMock(return_value=None)

        with (
            patch.object(backend_client_mod.httpx, "AsyncClient", return_value=client_instance),
            caplog.at_level(logging.WARNING),
        ):
            await backend_client_mod.notify_self_unpair()

        assert NETWORK_WARNING_LOG in caplog.text
