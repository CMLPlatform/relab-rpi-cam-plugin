"""Unit tests for the Pi-side LL-HLS proxy router."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from app.camera.routers import hls as hls_mod
from app.camera.routers.hls import _is_local_client, proxy_hls, start_preview, stop_preview
from app.relay.state import RelayRuntimeState
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
    pipeline.stop = AsyncMock()
    return pipeline


def _request(host: str = "192.168.2.10") -> MagicMock:
    request = MagicMock()
    request.client.host = host
    return request


class TestProxyHLS:
    """The Pi HLS proxy forwards to MediaMTX and returns the body verbatim."""

    async def test_m3u8_playlist_returned_as_hls_text(self) -> None:
        """The m3u8 playlist should come back with the correct content type."""
        playlist = b"#EXTM3U\n#EXT-X-VERSION:9\n#EXT-X-TARGETDURATION:1\n"
        response = _Response(
            200,
            content=playlist,
            headers={"content-type": HLS_M3U8_CONTENT_TYPE},
        )

        with _patch_httpx(response):
            relay_state = RelayRuntimeState()
            result = await proxy_hls(
                request=_request(),
                hls_path="cam-preview/index.m3u8",
                camera_manager=_camera_manager(),
                pipeline=_pipeline(),
                relay_state=relay_state,
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
            relay_state = RelayRuntimeState()
            result = await proxy_hls(
                request=_request(),
                hls_path="cam-preview/segment0.mp4",
                camera_manager=_camera_manager(),
                pipeline=_pipeline(),
                relay_state=relay_state,
            )

        assert result.body == segment
        assert result.media_type == HLS_MP4_CONTENT_TYPE

    async def test_preview_request_wakes_sleeping_encoder(self) -> None:
        """A local preview request starts the encoder before proxying to MediaMTX."""
        response = _Response(200, content=b"#EXTM3U\n", headers={"content-type": HLS_M3U8_CONTENT_TYPE})
        camera = MagicMock(name="camera")
        pipeline = _pipeline(running=False)

        with _patch_httpx(response):
            relay_state = RelayRuntimeState()
            await proxy_hls(
                request=_request(),
                hls_path="cam-preview/index.m3u8",
                camera_manager=_camera_manager(camera),
                pipeline=pipeline,
                relay_state=relay_state,
            )

        pipeline.start.assert_awaited_once_with(camera)

    async def test_non_preview_hls_request_does_not_wake_encoder(self) -> None:
        """Only the managed preview path controls the preview encoder."""
        response = _Response(200, content=b"#EXTM3U\n", headers={"content-type": HLS_M3U8_CONTENT_TYPE})
        pipeline = _pipeline(running=False)

        with _patch_httpx(response):
            relay_state = RelayRuntimeState()
            await proxy_hls(
                request=_request(),
                hls_path="other/index.m3u8",
                camera_manager=_camera_manager(),
                pipeline=pipeline,
                relay_state=relay_state,
            )

        pipeline.start.assert_not_called()

    async def test_public_client_is_rejected_before_proxying(self) -> None:
        """Unauthenticated HLS is limited to local network clients."""
        relay_state = RelayRuntimeState()
        with pytest.raises(HTTPException) as excinfo:
            await proxy_hls(
                request=_request("8.8.8.8"),
                hls_path="cam-preview/index.m3u8",
                camera_manager=_camera_manager(),
                pipeline=_pipeline(),
                relay_state=relay_state,
            )

        assert excinfo.value.status_code == 403
        assert relay_state.seconds_since_last_hls_activity() is None

    async def test_404_on_missing_stream(self) -> None:
        """MediaMTX 404 means the publisher hasn't attached yet."""
        relay_state = RelayRuntimeState()
        with _patch_httpx(_Response(404)), pytest.raises(HTTPException) as excinfo:
            await proxy_hls(
                request=_request(),
                hls_path="cam-preview/index.m3u8",
                camera_manager=_camera_manager(),
                pipeline=_pipeline(),
                relay_state=relay_state,
            )
        assert excinfo.value.status_code == 404
        assert HLS_PREVIEW_ENCODER_FRAGMENT in str(excinfo.value.detail)
        assert relay_state.seconds_since_last_hls_activity() is not None

    async def test_404_with_running_pipeline_does_not_recycle(self) -> None:
        """The proxy is pure — 404 is surfaced verbatim, recycle is the user's job via /preview/stop."""
        camera = MagicMock(name="camera")
        pipeline = _pipeline(running=True)
        relay_state = RelayRuntimeState()

        with _patch_httpx(_Response(404)), pytest.raises(HTTPException) as excinfo:
            await proxy_hls(
                request=_request(),
                hls_path="cam-preview/index.m3u8",
                camera_manager=_camera_manager(camera),
                pipeline=pipeline,
                relay_state=relay_state,
            )

        assert excinfo.value.status_code == 404
        pipeline.stop.assert_not_called()

    async def test_other_4xx_raises_502(self) -> None:
        """Anything other than 404 becomes a 502 upstream-error."""
        relay_state = RelayRuntimeState()
        with _patch_httpx(_Response(401)), pytest.raises(HTTPException) as excinfo:
            await proxy_hls(
                request=_request(),
                hls_path="cam-preview/index.m3u8",
                camera_manager=_camera_manager(),
                pipeline=_pipeline(),
                relay_state=relay_state,
            )
        assert excinfo.value.status_code == 502

    async def test_network_error_raises_503(self) -> None:
        """Connection failure to MediaMTX surfaces as a 503."""
        relay_state = RelayRuntimeState()
        with _patch_httpx(httpx.ConnectError("refused")), pytest.raises(HTTPException) as excinfo:
            await proxy_hls(
                request=_request(),
                hls_path="cam-preview/index.m3u8",
                camera_manager=_camera_manager(),
                pipeline=_pipeline(),
                relay_state=relay_state,
            )
        assert excinfo.value.status_code == 503


def _thumbnail_worker() -> MagicMock:
    worker = MagicMock()
    worker.refresh_once = AsyncMock(return_value=True)
    return worker


class TestPreviewStart:
    """POST /preview/start starts the encoder explicitly."""

    async def test_start_spawns_pipeline(self) -> None:
        """Starting the preview should start the encoder pipeline.

        Even if it would have been implicitly started by the first HLS request anyway.
        This allows the user to get ahead of the first request and have the preview ready to go immediately,
        rather than waiting for the first request to trigger startup and then waiting again for the first video segment
        to be generated before anything shows up in the preview.
        """
        camera = MagicMock(name="camera")
        pipeline = _pipeline(running=False)
        resp = await start_preview(
            request=_request(),
            camera_manager=_camera_manager(camera),
            pipeline=pipeline,
        )
        assert resp.status_code == 204
        pipeline.start.assert_awaited_once_with(camera)

    async def test_start_rejects_remote_client(self) -> None:
        """Test that starting the preview rejects remote clients."""
        with pytest.raises(HTTPException) as excinfo:
            await start_preview(
                request=_request("8.8.8.8"),
                camera_manager=_camera_manager(),
                pipeline=_pipeline(running=False),
            )
        assert excinfo.value.status_code == 403

    async def test_start_returns_503_when_camera_missing(self) -> None:
        """Test that starting the preview returns 503 if the camera is missing from the manager backend."""
        manager = MagicMock()
        manager.backend.camera = None
        with pytest.raises(HTTPException) as excinfo:
            await start_preview(
                request=_request(),
                camera_manager=manager,
                pipeline=_pipeline(running=False),
            )
        assert excinfo.value.status_code == 503


class TestPreviewStop:
    """POST /preview/stop stops the encoder and refreshes the cached thumbnail."""

    async def test_stop_halts_pipeline_and_refreshes_thumbnail(self) -> None:
        """Stopping the preview should stop the encoder and trigger a thumbnail refresh even if the pipeline is idle."""
        camera = MagicMock(name="camera")
        pipeline = _pipeline(running=True)
        worker = _thumbnail_worker()
        resp = await stop_preview(
            request=_request(),
            camera_manager=_camera_manager(camera),
            pipeline=pipeline,
            thumbnail_worker=worker,
        )
        assert resp.status_code == 204
        pipeline.stop.assert_awaited_once_with(camera)
        worker.refresh_once.assert_awaited_once()

    async def test_stop_refreshes_thumbnail_even_when_pipeline_idle(self) -> None:
        """Stopping the preview should still trigger a thumbnail refresh even if the pipeline isn't running."""
        pipeline = _pipeline(running=False)
        worker = _thumbnail_worker()
        resp = await stop_preview(
            request=_request(),
            camera_manager=_camera_manager(),
            pipeline=pipeline,
            thumbnail_worker=worker,
        )
        assert resp.status_code == 204
        pipeline.stop.assert_not_called()
        worker.refresh_once.assert_awaited_once()

    async def test_stop_rejects_remote_client(self) -> None:
        """Test that stopping the preview rejects remote clients."""
        with pytest.raises(HTTPException) as excinfo:
            await stop_preview(
                request=_request("8.8.8.8"),
                camera_manager=_camera_manager(),
                pipeline=_pipeline(),
                thumbnail_worker=_thumbnail_worker(),
            )
        assert excinfo.value.status_code == 403


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
