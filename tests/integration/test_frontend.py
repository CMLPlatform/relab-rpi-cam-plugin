"""Tests for frontend routes (landing page)."""

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import AsyncClient
from pydantic import AnyUrl
from relab_rpi_cam_models.stream import StreamMode

from app.api.dependencies.auth import verify_request
from app.api.routers import hls as hls_mod
from app.api.services.camera_manager import CameraManager
from app.main import app
from tests.constants import EXAMPLE_IMAGE_URL, HTML_CONTENT_TYPE, YOUTUBE_TEST_BROADCAST_URL

YOUTUBE_DOMAIN = "youtube.com"
HLS_PLAYLIST = "#EXTM3U\n"
HLS_ROUTE_PATH = "/hls/{hls_path:path}"


class TestHomepage:
    """Homepage routes."""

    async def test_homepage_returns_html(self, unauthed_client: AsyncClient) -> None:
        """Test that the homepage returns HTML content."""
        resp = await unauthed_client.get("/")
        assert resp.status_code == 200
        assert HTML_CONTENT_TYPE in resp.headers["content-type"]

    async def test_favicon_returns_ico(self, unauthed_client: AsyncClient) -> None:
        """Test that the favicon route returns an ICO file."""
        resp = await unauthed_client.get("/favicon.ico")
        assert resp.status_code == 200

    async def test_homepage_shows_youtube_link_when_stream_active(
        self,
        client: AsyncClient,
        camera_manager: CameraManager,
    ) -> None:
        """Homepage shows a Watch on YouTube link when a YouTube stream is active."""
        camera_manager.stream.mode = StreamMode.YOUTUBE
        camera_manager.stream.url = AnyUrl(YOUTUBE_TEST_BROADCAST_URL)
        resp = await client.get("/")
        assert resp.status_code == 200
        assert YOUTUBE_TEST_BROADCAST_URL in resp.text

    async def test_homepage_no_youtube_link_when_no_stream(
        self,
        client: AsyncClient,
    ) -> None:
        """Homepage does not show a YouTube link when no stream is active."""
        resp = await client.get("/")
        assert resp.status_code == 200
        assert YOUTUBE_DOMAIN not in resp.text

    async def test_homepage_shows_last_image_url_when_available(
        self,
        client: AsyncClient,
        camera_manager: CameraManager,
    ) -> None:
        """Homepage should surface the latest uploaded image URL in the recent-capture card."""
        camera_manager_any = cast("Any", camera_manager)
        camera_manager_any._last_image_url = AnyUrl(EXAMPLE_IMAGE_URL)

        resp = await client.get("/")

        assert resp.status_code == 200
        assert EXAMPLE_IMAGE_URL in resp.text

    async def test_hls_preview_proxy_is_available_without_auth(self, unauthed_client: AsyncClient) -> None:
        """Local preview HLS stays usable before pairing/login."""
        upstream = MagicMock()
        upstream.status_code = 200
        upstream.content = HLS_PLAYLIST.encode()
        upstream.headers = {"content-type": "application/vnd.apple.mpegurl"}

        http_client = MagicMock()
        http_client.get = AsyncMock(return_value=upstream)
        http_client.__aenter__ = AsyncMock(return_value=http_client)
        http_client.__aexit__ = AsyncMock(return_value=None)

        with patch.object(hls_mod.httpx, "AsyncClient", return_value=http_client):
            resp = await unauthed_client.get("/hls/cam-preview/index.m3u8")

        assert resp.status_code == 200
        assert resp.text == HLS_PLAYLIST

    def test_hls_preview_route_does_not_require_api_auth(self) -> None:
        """Regression test: HLS must stay available before pairing/login."""
        hls_route = next(route for route in app.routes if getattr(route, "path", "") == HLS_ROUTE_PATH)
        hls_route_any = cast("Any", hls_route)
        dependency_calls = [dependency.call for dependency in hls_route_any.dependant.dependencies]
        assert verify_request not in dependency_calls
