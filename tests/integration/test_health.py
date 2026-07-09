"""Tests for the unauthenticated local-network liveness probe."""

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.observability.router import SERVICE_NAME


class TestHealthz:
    """GET /healthz must answer LAN probes without an API key, and only them."""

    async def test_healthz_answers_without_an_api_key(self, unauthed_client: AsyncClient) -> None:
        """The app probes candidate LAN addresses before it holds the device key."""
        resp = await unauthed_client.get("/healthz")

        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "service": SERVICE_NAME}

    async def test_healthz_identifies_the_service(self, unauthed_client: AsyncClient) -> None:
        """A prober must be able to tell an RPi cam from any other host that returns 200."""
        resp = await unauthed_client.get("/healthz")

        assert resp.json()["service"] == SERVICE_NAME

    async def test_healthz_rejects_non_local_clients(self, test_app: FastAPI) -> None:
        """Liveness is a LAN-only surface; a remote visitor must not fingerprint the device."""
        transport = ASGITransport(app=test_app, client=("8.8.8.8", 40000))
        async with AsyncClient(transport=transport, base_url="http://test") as remote_client:
            resp = await remote_client.get("/healthz")

        assert resp.status_code == 403
