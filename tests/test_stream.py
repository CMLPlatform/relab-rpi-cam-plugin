"""Tests for streaming endpoints."""

from httpx import AsyncClient

from app.api.services.camera_manager import CameraManager


class TestStreamStatus:
    """Tests for GET /stream/status."""

    async def test_no_active_stream_returns_404(self, client: AsyncClient) -> None:
        resp = await client.get("/stream/status")
        assert resp.status_code == 404

    async def test_stream_redirect(self, client: AsyncClient) -> None:
        resp = await client.get("/stream", follow_redirects=False)
        assert resp.status_code == 307


class TestStreamStop:
    """Tests for DELETE /stream/stop."""

    async def test_stop_without_active_stream_returns_404(self, client: AsyncClient) -> None:
        resp = await client.delete("/stream/stop")
        assert resp.status_code == 404

    async def test_stop_wrong_mode_returns_404(
        self, client: AsyncClient, camera_manager: CameraManager
    ) -> None:
        camera_manager.stream.mode = "local"
        resp = await client.delete("/stream/stop", params={"mode": "youtube"})
        assert resp.status_code == 404


class TestHlsEndpoints:
    """Tests for HLS file serving."""

    async def test_hls_manifest_without_stream_returns_404(self, client: AsyncClient) -> None:
        resp = await client.get("/stream/hls")
        assert resp.status_code == 404

    async def test_hls_file_without_stream_returns_404(self, client: AsyncClient) -> None:
        resp = await client.get("/stream/hls/segment.ts")
        assert resp.status_code == 404
