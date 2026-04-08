"""Tests for streaming endpoints."""

from httpx import AsyncClient
from relab_rpi_cam_models.stream import StreamMode

from app.api.services.camera_manager import CameraManager


class TestStreamStatus:
    """Tests for GET /stream/status."""

    async def test_no_active_stream_returns_404(self, client: AsyncClient) -> None:
        """Test that if no stream is active, the endpoint returns 404."""
        resp = await client.get("/stream/status")
        assert resp.status_code == 404

    async def test_stream_redirect(self, client: AsyncClient) -> None:
        """Test that if a stream is active, the endpoint redirects to the stream URL."""
        resp = await client.get("/stream", follow_redirects=False)
        assert resp.status_code == 307


class TestStreamStop:
    """Tests for DELETE /stream/stop."""

    async def test_stop_without_active_stream_returns_404(self, client: AsyncClient) -> None:
        """Test that if no stream is active, the endpoint returns 404."""
        resp = await client.delete("/stream/stop")
        assert resp.status_code == 404

    async def test_stop_active_youtube_stream_returns_204(
        self,
        client: AsyncClient,
        camera_manager: CameraManager,
    ) -> None:
        """Test that stopping an active YouTube stream returns 204 and resets stream state."""
        camera_manager.stream.mode = StreamMode.YOUTUBE
        resp = await client.delete("/stream/stop")
        assert resp.status_code == 204
        assert not camera_manager.stream.is_active
