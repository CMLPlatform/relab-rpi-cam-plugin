"""Tests for image capture and retrieval endpoints."""

from pathlib import Path

import pytest
from httpx import AsyncClient

from app.core.config import settings


class TestGetImage:
    """Tests for GET /images/{image_id}."""

    async def test_missing_image_returns_404(self, client: AsyncClient) -> None:
        resp = await client.get("/images/nonexistent-id")
        assert resp.status_code == 404

    async def test_existing_image_returns_jpeg(self, client: AsyncClient, tmp_path: Path) -> None:
        # Point settings to tmp dir and create a fake JPEG
        original = settings.image_path
        settings.image_path = tmp_path
        (tmp_path / "abc123.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

        try:
            resp = await client.get("/images/abc123")
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "image/jpeg"
        finally:
            settings.image_path = original


class TestPreviewEndpoint:
    """Tests for GET /images/preview."""

    async def test_preview_returns_jpeg(self, client: AsyncClient) -> None:
        resp = await client.get("/images/preview")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/jpeg"
