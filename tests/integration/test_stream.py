"""Tests for streaming endpoints."""

from pathlib import Path

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from relab_rpi_cam_models.stream import StreamMode

from app.api.routers import stream as stream_router
from app.api.services.camera_manager import CameraManager
from app.core.config import settings


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

    async def test_stop_wrong_mode_returns_404(
        self,
        client: AsyncClient,
        camera_manager: CameraManager,
    ) -> None:
        """Test that if a stream is active but the mode doesn't match, the endpoint returns 404."""
        camera_manager.stream.mode = StreamMode.LOCAL
        resp = await client.delete("/stream/stop", params={"mode": "youtube"})
        assert resp.status_code == 404


class TestHlsEndpoints:
    """Tests for HLS file serving."""

    async def test_hls_manifest_without_stream_returns_404(self, client: AsyncClient) -> None:
        """Test that if no stream is active, the HLS manifest endpoint returns 404."""
        resp = await client.get("/stream/hls")
        assert resp.status_code == 404

    async def test_hls_file_without_stream_returns_404(self, client: AsyncClient) -> None:
        """Test that if no stream is active, the HLS file endpoint returns 404."""
        resp = await client.get("/stream/hls/segment.ts")
        assert resp.status_code == 404

    async def test_hls_file_serves_ts_segment(
        self,
        client: AsyncClient,
        camera_manager: CameraManager,
        tmp_path: Path,
    ) -> None:
        """Test that if a stream is active and a .ts file exists, the HLS file endpoint serves it."""
        original = settings.hls_path
        settings.hls_path = tmp_path
        camera_manager.stream.mode = StreamMode.LOCAL

        (tmp_path / "segment0.ts").write_bytes(b"\x00" * 100)
        try:
            resp = await client.get("/stream/hls/segment0.ts")
            assert resp.status_code == 200
        finally:
            settings.hls_path = original
            camera_manager.stream.mode = None

    async def test_hls_rejects_non_hls_file(
        self,
        client: AsyncClient,
        camera_manager: CameraManager,
        tmp_path: Path,
    ) -> None:
        """Test that if a stream is active, the HLS file endpoint rejects requests for non-.ts files."""
        original = settings.hls_path
        settings.hls_path = tmp_path
        camera_manager.stream.mode = StreamMode.LOCAL

        (tmp_path / "evil.txt").write_text("nope")
        try:
            resp = await client.get("/stream/hls/evil.txt")
            assert resp.status_code == 400
        finally:
            settings.hls_path = original
            camera_manager.stream.mode = None

    async def test_hls_rejects_path_traversal(
        self,
        camera_manager: CameraManager,
        tmp_path: Path,
    ) -> None:
        """Test that if a stream is active, the HLS file endpoint rejects path traversal attempts."""
        original = settings.hls_path
        settings.hls_path = tmp_path
        camera_manager.stream.mode = StreamMode.LOCAL

        try:
            with pytest.raises(HTTPException) as exc_info:
                await stream_router.hls_file("../../../etc/passwd", camera_manager)
            assert exc_info.value.status_code == 403
        finally:
            settings.hls_path = original
            camera_manager.stream.mode = None

    async def test_hls_manifest_redirect(
        self,
        client: AsyncClient,
        camera_manager: CameraManager,
    ) -> None:
        """Test that if a stream is active, the HLS manifest endpoint redirects to the manifest URL."""
        camera_manager.stream.mode = StreamMode.LOCAL
        try:
            resp = await client.get("/stream/hls", follow_redirects=False)
            assert resp.status_code == 307
        finally:
            camera_manager.stream.mode = None
