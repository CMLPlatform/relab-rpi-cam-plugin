"""Tests for frontend routes (landing page)."""

from httpx import AsyncClient
from pydantic import AnyUrl
from relab_rpi_cam_models.stream import StreamMode

from app.api.services.camera_manager import CameraManager
from tests.constants import HTML_CONTENT_TYPE

YOUTUBE_WATCH_URL = "https://youtube.com/watch?v=TEST_BROADCAST_KEY_123"
YOUTUBE_DOMAIN = "youtube.com"


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
        camera_manager.stream.url = AnyUrl(YOUTUBE_WATCH_URL)
        resp = await client.get("/")
        assert resp.status_code == 200
        assert YOUTUBE_WATCH_URL in resp.text

    async def test_homepage_no_youtube_link_when_no_stream(
        self,
        client: AsyncClient,
    ) -> None:
        """Homepage does not show a YouTube link when no stream is active."""
        resp = await client.get("/")
        assert resp.status_code == 200
        assert YOUTUBE_DOMAIN not in resp.text
