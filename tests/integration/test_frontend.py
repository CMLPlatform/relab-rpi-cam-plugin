"""Tests for frontend routes (landing, stream viewer)."""

from httpx import AsyncClient
from pydantic import AnyUrl
from relab_rpi_cam_models.stream import StreamMode

from app.api.dependencies.auth import create_session
from app.api.services.camera_manager import CameraManager
from app.core.config import settings
from tests.constants import HTML_CONTENT_TYPE

LOGIN_PATH = "/login"
STREAM_WATCH_YOUTUBE_PATH = "/stream/watch/youtube"
STREAM_WATCH_PATH = "/stream/watch"
EMBED_URL = "https://www.youtube.com/embed/TEST_BROADCAST_KEY_123"


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
        resp = await unauthed_client.get(STREAM_WATCH_YOUTUBE_PATH, follow_redirects=False)
        assert resp.status_code == 307
        assert LOGIN_PATH in resp.headers["location"]

    async def test_authenticated_youtube_viewer(
        self,
        client: AsyncClient,
        camera_manager: CameraManager,
    ) -> None:
        """Test that the YouTube stream viewer page returns HTML content and includes the embed URL."""
        client.cookies.set(settings.session_cookie_name, create_session())
        camera_manager.stream.mode = StreamMode.YOUTUBE
        camera_manager.stream.url = AnyUrl("https://youtube.com/watch?v=TEST_BROADCAST_KEY_123")
        resp = await client.get(STREAM_WATCH_YOUTUBE_PATH)
        assert resp.status_code == 200
        assert HTML_CONTENT_TYPE in resp.headers["content-type"]
        assert EMBED_URL in resp.text

    async def test_watch_redirect_to_youtube(self, unauthed_client: AsyncClient) -> None:
        """Test that /stream/watch redirects to the YouTube viewer."""
        unauthed_client.cookies.set(settings.session_cookie_name, create_session())
        resp = await unauthed_client.get(STREAM_WATCH_PATH, follow_redirects=False)
        assert resp.status_code == 303
        assert STREAM_WATCH_YOUTUBE_PATH in resp.headers["location"]
