"""Tests for streaming endpoints."""

from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from pydantic import AnyUrl, SecretStr
from relab_rpi_cam_models.stream import StreamMode

from app.api.exceptions import ActiveStreamError
from app.api.schemas.streaming import YoutubeConfigRequiredError, YoutubeStreamConfig
from app.api.services.camera_manager import CameraManager
from app.api.services.stream_state import ActiveStreamState
from tests.constants import YOUTUBE_WATCH_URL_PREFIX

YOUTUBE_CONFIG_KEY = "youtube_config"
VALID_BODY = {"stream_key": "secret-stream", "broadcast_key": "public-broadcast"}
ENCODER_STUCK_MSG = "encoder stuck"


class TestStreamStatus:
    """Tests for GET /stream."""

    async def test_no_active_stream_returns_404(self, client: AsyncClient) -> None:
        """Test that if no stream is active, the endpoint returns 404."""
        resp = await client.get("/stream")
        assert resp.status_code == 404

    async def test_status_does_not_leak_provider_secrets(
        self,
        client: AsyncClient,
        camera_manager: CameraManager,
    ) -> None:
        """Public stream state should not include provider-specific secret config."""
        youtube_config = YoutubeStreamConfig(
            stream_key=SecretStr("secret-stream"),
            broadcast_key=SecretStr("public-broadcast"),
        )
        await camera_manager.start_streaming(StreamMode.YOUTUBE, youtube_config=youtube_config)

        resp = await client.get("/stream")

        assert resp.status_code == 200
        assert YOUTUBE_CONFIG_KEY not in resp.json()


class TestStreamStart:
    """Tests for POST /stream."""

    async def test_start_stream_returns_201(self, client: AsyncClient) -> None:
        """Valid YouTube config should start a stream."""
        resp = await client.post("/stream", json=VALID_BODY)
        assert resp.status_code == 201
        assert resp.json()["mode"] == StreamMode.YOUTUBE

    async def test_missing_body_returns_422(self, client: AsyncClient) -> None:
        """The YouTube config body is required."""
        resp = await client.post("/stream")
        assert resp.status_code == 422

    async def test_openapi_includes_youtube_example(self, client: AsyncClient) -> None:
        """OpenAPI should include the YouTube request example."""
        resp = await client.get("/openapi.json")
        assert resp.status_code == 200
        request_body = resp.json()["paths"]["/stream"]["post"]["requestBody"]["content"]["application/json"]["schema"]
        assert request_body["$ref"].endswith("YoutubeStreamConfig")


class TestStreamStop:
    """Tests for DELETE /stream."""

    async def test_stop_without_active_stream_returns_404(self, client: AsyncClient) -> None:
        """Test that if no stream is active, the endpoint returns 404."""
        resp = await client.delete("/stream")
        assert resp.status_code == 404

    async def test_stop_active_youtube_stream_returns_204(
        self,
        client: AsyncClient,
        camera_manager: CameraManager,
    ) -> None:
        """Test that stopping an active YouTube stream returns 204 and resets stream state."""
        camera_manager.stream.mode = StreamMode.YOUTUBE
        resp = await client.delete("/stream")
        assert resp.status_code == 204
        assert not camera_manager.stream.is_active

    async def test_stop_stream_returns_500_on_runtime_error(
        self,
        client: AsyncClient,
        camera_manager: CameraManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A RuntimeError during shutdown should surface as an HTTP 500."""
        camera_manager.stream.mode = StreamMode.YOUTUBE
        monkeypatch.setattr(
            camera_manager,
            "stop_streaming",
            AsyncMock(side_effect=RuntimeError(ENCODER_STUCK_MSG)),
        )
        resp = await client.delete("/stream")
        assert resp.status_code == 500
        assert ENCODER_STUCK_MSG in resp.json().get("detail", "")


class TestStreamStartErrorPaths:
    """``POST /stream`` should translate backend exceptions into HTTP errors."""

    async def test_missing_youtube_config_returns_400(
        self,
        client: AsyncClient,
        camera_manager: CameraManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``YoutubeConfigRequiredError`` from the backend -> 400."""
        monkeypatch.setattr(
            camera_manager,
            "start_streaming",
            AsyncMock(side_effect=YoutubeConfigRequiredError()),
        )
        resp = await client.post("/stream", json=VALID_BODY)
        assert resp.status_code == 400

    async def test_already_active_stream_returns_409(
        self,
        client: AsyncClient,
        camera_manager: CameraManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``ActiveStreamError`` from the backend -> 409."""
        active = ActiveStreamState(mode=StreamMode.YOUTUBE, url=AnyUrl(f"{YOUTUBE_WATCH_URL_PREFIX}abc"))
        monkeypatch.setattr(
            camera_manager,
            "start_streaming",
            AsyncMock(side_effect=ActiveStreamError(active)),
        )
        resp = await client.post("/stream", json=VALID_BODY)
        assert resp.status_code == 409
