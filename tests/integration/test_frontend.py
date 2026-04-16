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
HLS_ROUTE_PATH = "/preview/hls/{hls_path:path}"
DEFAULT_CSP = "default-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
FRAME_OPTIONS_DENY = "DENY"
SETUP_CSP_INLINE = "'unsafe-inline'"
SETUP_CSP_CDN = "https://cdn.jsdelivr.net"
DOCS_FAVICON_HOST = "https://fastapi.tiangolo.com"
THEME_TOGGLE_MARKER = "data-theme-toggle"
LOGO_SRC = "/static/logo.png"
SITE_JS_SRC = "/static/site.js"
SETUP_LINK_TEXT = ">Setup</a>"
API_DOCS_LINK_TEXT = ">API Docs</a>"
HOMEPAGE_SECONDARY_COPY = "Start a live local preview when you want to check framing or focus."
OPEN_FULL_IMAGE_TEXT = "Open full image"
THEME_AUTO_LABEL = "Theme: Auto"


class TestHomepage:
    """Homepage routes."""

    async def test_homepage_returns_html(self, unauthed_client: AsyncClient) -> None:
        """Test that the homepage returns HTML content."""
        resp = await unauthed_client.get("/")
        assert resp.status_code == 200
        assert HTML_CONTENT_TYPE in resp.headers["content-type"]

    async def test_homepage_sets_relaxed_csp_for_embedded_preview_assets(self, unauthed_client: AsyncClient) -> None:
        """The landing page CSP should allow its inline script and hls.js dependency."""
        resp = await unauthed_client.get("/")
        assert resp.headers["x-frame-options"] == FRAME_OPTIONS_DENY
        assert SETUP_CSP_CDN in resp.headers["content-security-policy"]
        assert SETUP_CSP_INLINE in resp.headers["content-security-policy"]

    async def test_homepage_renders_shared_header_assets_and_theme_control(self, unauthed_client: AsyncClient) -> None:
        """The landing page should expose the shared brand header and theme chooser."""
        resp = await unauthed_client.get("/")
        assert resp.status_code == 200
        assert THEME_TOGGLE_MARKER in resp.text
        assert THEME_AUTO_LABEL in resp.text
        assert LOGO_SRC in resp.text
        assert SITE_JS_SRC in resp.text

    async def test_homepage_keeps_primary_actions_in_header_only(self, unauthed_client: AsyncClient) -> None:
        """The homepage should avoid duplicating setup and docs navigation in the hero."""
        resp = await unauthed_client.get("/")
        assert resp.status_code == 200
        assert resp.text.count(SETUP_LINK_TEXT) == 1
        assert resp.text.count(API_DOCS_LINK_TEXT) == 1
        assert HOMEPAGE_SECONDARY_COPY in resp.text

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
        assert OPEN_FULL_IMAGE_TEXT in resp.text

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
            resp = await unauthed_client.get("/preview/hls/cam-preview/index.m3u8")

        assert resp.status_code == 200
        assert resp.text == HLS_PLAYLIST

    def test_hls_preview_route_does_not_require_api_auth(self) -> None:
        """Regression test: HLS must stay available before pairing/login."""
        hls_route = next(route for route in app.routes if getattr(route, "path", "") == HLS_ROUTE_PATH)
        hls_route_any = cast("Any", hls_route)
        dependency_calls = [dependency.call for dependency in hls_route_any.dependant.dependencies]
        assert verify_request not in dependency_calls

    async def test_hls_preview_route_sets_default_csp(self, unauthed_client: AsyncClient) -> None:
        """API-style routes should use the tighter default CSP."""
        upstream = MagicMock()
        upstream.status_code = 200
        upstream.content = HLS_PLAYLIST.encode()
        upstream.headers = {"content-type": "application/vnd.apple.mpegurl"}

        http_client = MagicMock()
        http_client.get = AsyncMock(return_value=upstream)
        http_client.__aenter__ = AsyncMock(return_value=http_client)
        http_client.__aexit__ = AsyncMock(return_value=None)

        with patch.object(hls_mod.httpx, "AsyncClient", return_value=http_client):
            resp = await unauthed_client.get("/preview/hls/cam-preview/index.m3u8")

        assert resp.headers["content-security-policy"] == DEFAULT_CSP

    async def test_docs_route_allows_swagger_assets_in_csp(self, unauthed_client: AsyncClient) -> None:
        """Swagger docs should receive a CSP that permits the bundled FastAPI assets."""
        resp = await unauthed_client.get("/docs")
        assert resp.status_code == 200
        assert SETUP_CSP_CDN in resp.headers["content-security-policy"]
        assert DOCS_FAVICON_HOST in resp.headers["content-security-policy"]
        assert SETUP_CSP_INLINE in resp.headers["content-security-policy"]
