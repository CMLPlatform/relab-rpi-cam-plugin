"""Unit tests for the Pi-side LL-HLS proxy router."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from app.api.routers import hls as hls_mod
from app.api.routers.hls import _is_local_client, proxy_hls
from app.utils import relay_state
from tests.constants import HLS_M3U8_CONTENT_TYPE, HLS_MP4_CONTENT_TYPE, HLS_PREVIEW_ENCODER_FRAGMENT

if TYPE_CHECKING:
    from contextlib import AbstractContextManager


class _Response:
    """Minimal stand-in for an httpx response returned by the proxy client."""

    def __init__(
        self,
        status_code: int,
        content: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}


def _patch_httpx(response: _Response | Exception) -> AbstractContextManager[MagicMock]:
    client = MagicMock()
    if isinstance(response, Exception):
        client.get = AsyncMock(side_effect=response)
    else:
        client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return patch.object(hls_mod.httpx, "AsyncClient", return_value=client)


def _camera_manager(camera: MagicMock | None = None) -> MagicMock:
    manager = MagicMock()
    manager.backend.camera = camera or MagicMock(name="camera")
    return manager


def _pipeline(*, running: bool = True) -> MagicMock:
    pipeline = MagicMock()
    pipeline.is_running = running
    pipeline.start = AsyncMock()
    return pipeline


def _request(host: str = "192.168.2.10") -> MagicMock:
    request = MagicMock()
    request.client.host = host
    return request


class TestProxyHLS:
    """The Pi HLS proxy forwards to MediaMTX and returns the body verbatim."""

    def setup_method(self) -> None:
        """Reset HLS activity between tests."""
        relay_state.reset_for_tests()

    async def test_m3u8_playlist_returned_as_hls_text(self) -> None:
        """The m3u8 playlist should come back with the correct content type."""
        playlist = b"#EXTM3U\n#EXT-X-VERSION:9\n#EXT-X-TARGETDURATION:1\n"
        response = _Response(
            200,
            content=playlist,
            headers={"content-type": HLS_M3U8_CONTENT_TYPE},
        )

        with _patch_httpx(response):
            result = await proxy_hls(
                request=_request(),
                hls_path="cam-preview/index.m3u8",
                camera_manager=_camera_manager(),
                pipeline=_pipeline(),
            )

        assert result.body == playlist
        assert result.media_type == HLS_M3U8_CONTENT_TYPE
        assert relay_state.seconds_since_last_hls_activity() is not None

    async def test_video_segment_returned_as_binary(self) -> None:
        """Binary segments (.mp4) come back with video/mp4 content-type."""
        segment = b"\x00\x00\x00\x18ftypmp42"
        response = _Response(
            200,
            content=segment,
            headers={"content-type": HLS_MP4_CONTENT_TYPE},
        )

        with _patch_httpx(response):
            result = await proxy_hls(
                request=_request(),
                hls_path="cam-preview/segment0.mp4",
                camera_manager=_camera_manager(),
                pipeline=_pipeline(),
            )

        assert result.body == segment
        assert result.media_type == HLS_MP4_CONTENT_TYPE

    async def test_preview_request_wakes_sleeping_encoder(self) -> None:
        """A local preview request starts the encoder before proxying to MediaMTX."""
        response = _Response(200, content=b"#EXTM3U\n", headers={"content-type": HLS_M3U8_CONTENT_TYPE})
        camera = MagicMock(name="camera")
        pipeline = _pipeline(running=False)

        with _patch_httpx(response):
            await proxy_hls(
                request=_request(),
                hls_path="cam-preview/index.m3u8",
                camera_manager=_camera_manager(camera),
                pipeline=pipeline,
            )

        pipeline.start.assert_awaited_once_with(camera)

    async def test_non_preview_hls_request_does_not_wake_encoder(self) -> None:
        """Only the managed preview path controls the preview encoder."""
        response = _Response(200, content=b"#EXTM3U\n", headers={"content-type": HLS_M3U8_CONTENT_TYPE})
        pipeline = _pipeline(running=False)

        with _patch_httpx(response):
            await proxy_hls(
                request=_request(),
                hls_path="other/index.m3u8",
                camera_manager=_camera_manager(),
                pipeline=pipeline,
            )

        pipeline.start.assert_not_called()

    async def test_public_client_is_rejected_before_proxying(self) -> None:
        """Unauthenticated HLS is limited to local network clients."""
        with pytest.raises(HTTPException) as excinfo:
            await proxy_hls(
                request=_request("8.8.8.8"),
                hls_path="cam-preview/index.m3u8",
                camera_manager=_camera_manager(),
                pipeline=_pipeline(),
            )

        assert excinfo.value.status_code == 403
        assert relay_state.seconds_since_last_hls_activity() is None

    async def test_404_on_missing_stream(self) -> None:
        """MediaMTX 404 means the publisher hasn't attached yet."""
        with _patch_httpx(_Response(404)), pytest.raises(HTTPException) as excinfo:
            await proxy_hls(
                request=_request(),
                hls_path="cam-preview/index.m3u8",
                camera_manager=_camera_manager(),
                pipeline=_pipeline(),
            )
        assert excinfo.value.status_code == 404
        assert HLS_PREVIEW_ENCODER_FRAGMENT in str(excinfo.value.detail)
        assert relay_state.seconds_since_last_hls_activity() is not None

    async def test_other_4xx_raises_502(self) -> None:
        """Anything other than 404 becomes a 502 upstream-error."""
        with _patch_httpx(_Response(401)), pytest.raises(HTTPException) as excinfo:
            await proxy_hls(
                request=_request(),
                hls_path="cam-preview/index.m3u8",
                camera_manager=_camera_manager(),
                pipeline=_pipeline(),
            )
        assert excinfo.value.status_code == 502

    async def test_network_error_raises_503(self) -> None:
        """Connection failure to MediaMTX surfaces as a 503."""
        with _patch_httpx(httpx.ConnectError("refused")), pytest.raises(HTTPException) as excinfo:
            await proxy_hls(
                request=_request(),
                hls_path="cam-preview/index.m3u8",
                camera_manager=_camera_manager(),
                pipeline=_pipeline(),
            )
        assert excinfo.value.status_code == 503


class TestLocalClientDetection:
    """Client IP policy for unauthenticated HLS preview."""

    @pytest.mark.parametrize("host", ["127.0.0.1", "::1", "192.168.2.10", "10.0.0.5", "172.20.0.2", "169.254.1.2"])
    def test_local_addresses_are_allowed(self, host: str) -> None:
        """Loopback, private, and link-local addresses may use local preview."""
        assert _is_local_client(host) is True

    @pytest.mark.parametrize("host", ["8.8.8.8", "2001:4860:4860::8888", None])
    def test_public_or_missing_addresses_are_rejected(self, host: str | None) -> None:
        """Public and missing client addresses may not use local preview."""
        assert _is_local_client(host) is False
