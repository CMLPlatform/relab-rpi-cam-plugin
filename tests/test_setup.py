"""Tests for setup page and QR code endpoints."""

from httpx import AsyncClient


class TestSetupPage:
    """Tests for GET /setup (no auth required)."""

    async def test_setup_page_returns_html(self, unauthed_client: AsyncClient) -> None:
        resp = await unauthed_client.get("/setup")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_setup_page_contains_title(self, unauthed_client: AsyncClient) -> None:
        resp = await unauthed_client.get("/setup")
        assert "RPi Camera Setup" in resp.text

    async def test_setup_page_contains_camera_url(self, unauthed_client: AsyncClient) -> None:
        resp = await unauthed_client.get("/setup")
        assert "Camera URL" in resp.text


class TestQrEndpoint:
    """Tests for GET /qr-setup."""

    async def test_qr_returns_png(self, unauthed_client: AsyncClient) -> None:
        resp = await unauthed_client.get("/qr-setup")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
