"""Unit tests for the MediaMTX runtime control client."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.api.services import mediamtx_client as mediamtx_mod
from app.api.services.mediamtx_client import MediaMTXAPIError, MediaMTXClient


class _Response:
    """Minimal stand-in for an httpx Response."""

    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


def _patch_client(response: _Response | Exception) -> object:
    """Patch ``mediamtx_client.httpx.AsyncClient`` to return the fake response."""
    client = MagicMock()
    if isinstance(response, Exception):
        client.patch = AsyncMock(side_effect=response)
    else:
        client.patch = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return patch.object(mediamtx_mod.httpx, "AsyncClient", return_value=client)


class TestSetYoutubeEgress:
    """Attach the ``runOnReady`` ffmpeg command to a MediaMTX path."""

    async def test_happy_path_sends_youtube_ffmpeg_command(self) -> None:
        """The PATCH body embeds the stream key in a YouTube RTMPS ffmpeg command."""
        response = _Response(200)
        with _patch_client(response) as async_client_ctor:
            client = MediaMTXClient(base_url="http://mediamtx:9997")
            await client.set_youtube_egress("cam-hires", "abcd-efgh-ijkl")

        mocked = async_client_ctor.return_value
        mocked.patch.assert_awaited_once()
        url = mocked.patch.await_args.args[0]
        body = mocked.patch.await_args.kwargs["json"]
        assert url == "http://mediamtx:9997/v3/config/paths/patch/cam-hires"
        assert "rtmps://a.rtmps.youtube.com:443/live2/abcd-efgh-ijkl" in body["runOnReady"]
        assert "ffmpeg" in body["runOnReady"]
        assert "-c:v copy" in body["runOnReady"]
        assert "anullsrc" in body["runOnReady"]
        assert body["runOnReadyRestart"] is False

    async def test_404_is_treated_as_a_soft_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """A 404 on patch means the path isn't declared yet — log and move on."""
        with _patch_client(_Response(404)), caplog.at_level("WARNING"):
            client = MediaMTXClient()
            await client.set_youtube_egress("ghost-path", "key")
        assert "missing path" in caplog.text

    async def test_5xx_raises_mediamtx_api_error(self) -> None:
        """A 5xx surfaces as a ``MediaMTXAPIError`` so callers can bubble it up."""
        with _patch_client(_Response(500, text="internal server error")), pytest.raises(MediaMTXAPIError) as excinfo:
            client = MediaMTXClient()
            await client.set_youtube_egress("cam-hires", "key")
        assert "HTTP 500" in str(excinfo.value)

    async def test_network_error_wraps_as_mediamtx_api_error(self) -> None:
        """A connection failure surfaces as ``MediaMTXAPIError``."""
        with _patch_client(httpx.ConnectError("refused")), pytest.raises(MediaMTXAPIError) as excinfo:
            client = MediaMTXClient()
            await client.set_youtube_egress("cam-hires", "key")
        assert "unreachable" in str(excinfo.value).lower()


class TestClearEgress:
    """Teardown is an idempotent no-op that zeros the runOnReady field."""

    async def test_clear_egress_sends_empty_runonready(self) -> None:
        """Clearing an egress writes an empty ``runOnReady`` to MediaMTX."""
        with _patch_client(_Response(200)) as async_client_ctor:
            client = MediaMTXClient()
            await client.clear_egress("cam-hires")
        body = async_client_ctor.return_value.patch.await_args.kwargs["json"]
        assert body["runOnReady"] == ""
        assert body["runOnReadyRestart"] is False
