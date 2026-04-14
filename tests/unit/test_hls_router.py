"""Unit tests for the Pi-side LL-HLS proxy router."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from app.api.routers import hls as hls_mod
from app.api.routers.hls import proxy_hls


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


def _patch_httpx(response: _Response | Exception) -> object:
    client = MagicMock()
    if isinstance(response, Exception):
        client.get = AsyncMock(side_effect=response)
    else:
        client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return patch.object(hls_mod.httpx, "AsyncClient", return_value=client)


class TestProxyHLS:
    """The Pi HLS proxy forwards to MediaMTX and returns the body verbatim."""

    async def test_m3u8_playlist_returned_as_hls_text(self) -> None:
        """The m3u8 playlist should come back with the correct content type."""
        playlist = b"#EXTM3U\n#EXT-X-VERSION:9\n#EXT-X-TARGETDURATION:1\n"
        response = _Response(
            200,
            content=playlist,
            headers={"content-type": "application/vnd.apple.mpegurl"},
        )

        with _patch_httpx(response):
            result = await proxy_hls(hls_path="cam-preview/index.m3u8")

        assert result.body == playlist
        assert result.media_type == "application/vnd.apple.mpegurl"

    async def test_video_segment_returned_as_binary(self) -> None:
        """Binary segments (.mp4) come back with video/mp4 content-type."""
        segment = b"\x00\x00\x00\x18ftypmp42"
        response = _Response(
            200,
            content=segment,
            headers={"content-type": "video/mp4"},
        )

        with _patch_httpx(response):
            result = await proxy_hls(hls_path="cam-preview/segment0.mp4")

        assert result.body == segment
        assert result.media_type == "video/mp4"

    async def test_404_on_missing_stream(self) -> None:
        """MediaMTX 404 means the publisher hasn't attached yet."""
        with _patch_httpx(_Response(404)), pytest.raises(HTTPException) as excinfo:
            await proxy_hls(hls_path="cam-preview/index.m3u8")
        assert excinfo.value.status_code == 404
        assert "preview encoder" in str(excinfo.value.detail)

    async def test_other_4xx_raises_502(self) -> None:
        """Anything other than 404 becomes a 502 upstream-error."""
        with _patch_httpx(_Response(401)), pytest.raises(HTTPException) as excinfo:
            await proxy_hls(hls_path="cam-preview/index.m3u8")
        assert excinfo.value.status_code == 502

    async def test_network_error_raises_503(self) -> None:
        """Connection failure to MediaMTX surfaces as a 503."""
        with _patch_httpx(httpx.ConnectError("refused")), pytest.raises(HTTPException) as excinfo:
            await proxy_hls(hls_path="cam-preview/index.m3u8")
        assert excinfo.value.status_code == 503
