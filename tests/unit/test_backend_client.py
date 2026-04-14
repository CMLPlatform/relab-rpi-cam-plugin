"""Tests for the Pi-side backend upload client."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from app.core.config import settings
from app.utils import backend_client as backend_client_mod
from app.utils.backend_client import BackendUploadError, upload_image


@pytest.fixture(autouse=True)
def _relay_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide the minimum relay-credential state that backend_client needs.

    ``settings.relay_enabled`` is a computed property that ANDs the four relay
    fields; setting all four makes it True and lets upload_image proceed.
    """
    monkeypatch.setattr(settings, "pairing_backend_url", "https://backend.example/")
    monkeypatch.setattr(settings, "relay_backend_url", "wss://backend.example/ws")
    monkeypatch.setattr(settings, "relay_camera_id", str(uuid4()))
    monkeypatch.setattr(settings, "relay_private_key_pem", "fake-pem")
    monkeypatch.setattr(settings, "relay_key_id", "fake-kid")
    monkeypatch.setattr(backend_client_mod, "build_device_assertion", lambda: "fake.jwt.token")


def _fake_response(status: int, body: object) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json = MagicMock(return_value=body)
    resp.text = str(body)
    return resp


def _patch_async_client(response: MagicMock) -> object:
    client_instance = MagicMock()
    client_instance.post = AsyncMock(return_value=response)
    client_instance.__aenter__ = AsyncMock(return_value=client_instance)
    client_instance.__aexit__ = AsyncMock(return_value=None)
    return patch.object(backend_client_mod.httpx, "AsyncClient", return_value=client_instance)


class TestUploadImage:
    """Tests for upload_image happy and failure paths."""

    async def test_happy_path_returns_uploaded_info(self) -> None:
        """A 200 JSON response with image_id+image_url should populate UploadedImageInfo."""
        response = _fake_response(
            200,
            {"image_id": "server-abc", "image_url": "https://backend.example/images/abc.jpg"},
        )
        with _patch_async_client(response):
            result = await upload_image(
                image_bytes=b"\xff\xd8fake-jpg",
                filename="test.jpg",
                capture_metadata={"width": 1920},
                upload_metadata={"product_id": 1},
            )

        assert result.image_id == "server-abc"
        assert str(result.image_url) == "https://backend.example/images/abc.jpg"

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

    async def test_requires_relay_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Uploading before the device is paired must fail cleanly.

        ``relay_enabled`` is a computed property — clearing any one of the four
        underlying fields flips it to False.
        """
        monkeypatch.setattr(settings, "relay_camera_id", "")
        with pytest.raises(BackendUploadError, match="unpaired"):
            await upload_image(
                image_bytes=b"\xff\xd8",
                filename="test.jpg",
                capture_metadata={},
                upload_metadata={},
            )
