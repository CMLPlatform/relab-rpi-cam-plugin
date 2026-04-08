"""Tests for frontend routes (landing, stream viewer)."""

from httpx import AsyncClient
from relab_rpi_cam_models.stream import StreamMode

from app.api.services.camera_manager import CameraManager
from app.core.config import settings
from tests.constants import HTML_CONTENT_TYPE

LOGIN_PATH = "/login"
STREAM_WATCH_LOCAL_PATH = "/stream/watch/local"
STREAM_WATCH_YOUTUBE_PATH = "/stream/watch/youtube"
STREAM_WATCH_PATH = "/stream/watch"


class TestHomepage:
    """Homepage and login page routes."""

    async def test_homepage_returns_html(self, unauthed_client: AsyncClient) -> None:
        """Test that the homepage returns HTML content."""
        resp = await unauthed_client.get("/")
        assert resp.status_code == 200
        assert HTML_CONTENT_TYPE in resp.headers["content-type"]

    async def test_login_page_returns_html(self, unauthed_client: AsyncClient) -> None:
        """Test that the login page returns HTML content."""
        resp = await unauthed_client.get("/login?redirect_url=/")
        assert resp.status_code == 200
        assert HTML_CONTENT_TYPE in resp.headers["content-type"]

    async def test_favicon_returns_ico(self, unauthed_client: AsyncClient) -> None:
        """Test that the favicon route returns an ICO file."""
        resp = await unauthed_client.get("/favicon.ico")
        assert resp.status_code == 200


class TestStreamViewer:
    """Stream viewer routes require cookie auth."""

    async def test_unauthenticated_redirects_to_login(self, unauthed_client: AsyncClient) -> None:
        """Test that unauthenticated access to stream viewer routes redirects to login."""
        resp = await unauthed_client.get(STREAM_WATCH_LOCAL_PATH, follow_redirects=False)
        assert resp.status_code == 307
        assert LOGIN_PATH in resp.headers["location"]

    async def test_authenticated_youtube_viewer(self, unauthed_client: AsyncClient) -> None:
        """Test that the YouTube stream viewer page returns HTML content for authenticated users."""
        # Set a cookie to simulate auth
        unauthed_client.cookies.set(settings.auth_key_name, "any-value")
        resp = await unauthed_client.get(STREAM_WATCH_YOUTUBE_PATH)
        assert resp.status_code == 200
        assert HTML_CONTENT_TYPE in resp.headers["content-type"]

    async def test_authenticated_local_viewer(self, unauthed_client: AsyncClient) -> None:
        """Test that the local stream viewer page returns HTML content for authenticated users."""
        unauthed_client.cookies.set(settings.auth_key_name, "any-value")
        resp = await unauthed_client.get(STREAM_WATCH_LOCAL_PATH)
        assert resp.status_code == 200
        assert HTML_CONTENT_TYPE in resp.headers["content-type"]

    async def test_watch_redirect(self, unauthed_client: AsyncClient) -> None:
        """Test that the generic /stream/watch route redirects to the appropriate viewer based on stream mode."""
        unauthed_client.cookies.set(settings.auth_key_name, "any-value")
        resp = await unauthed_client.get(STREAM_WATCH_PATH, follow_redirects=False)
        assert resp.status_code == 303

    async def test_watch_redirects_to_youtube_when_active(
        self,
        client: AsyncClient,
        camera_manager: CameraManager,
    ) -> None:
        """Test that /stream/watch redirects to YouTube viewer when stream mode is YOUTUBE."""
        client.cookies.set(settings.auth_key_name, "any-value")
        camera_manager.stream.mode = StreamMode.YOUTUBE
        resp = await client.get(STREAM_WATCH_PATH, follow_redirects=False)
        assert resp.status_code == 303
        assert STREAM_WATCH_YOUTUBE_PATH in resp.headers["location"]
