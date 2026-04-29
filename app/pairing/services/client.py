"""HTTP client helpers for the backend-facing pairing flow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from relab_rpi_cam_models import DevicePublicKeyJWK, PairingPollResponse, PairingRegisterRequest

if TYPE_CHECKING:
    import httpx

PAIRING_REGISTER_PATH = "/v1/plugins/rpi-cam/pairing/register"
PAIRING_POLL_PATH = "/v1/plugins/rpi-cam/pairing/poll"


@dataclass(frozen=True)
class PairingClient:
    """Own the plugin's HTTP calls to the backend pairing API."""

    http_client: httpx.AsyncClient
    base_url: str

    async def register(
        self,
        *,
        code: str,
        fingerprint: str,
        public_key_jwk: DevicePublicKeyJWK | dict[str, str],
        key_id: str,
    ) -> httpx.Response:
        """Submit one register request."""
        request = PairingRegisterRequest(
            code=code,
            rpi_fingerprint=fingerprint,
            public_key_jwk=DevicePublicKeyJWK.model_validate(public_key_jwk),
            key_id=key_id,
        )
        return await self.http_client.post(
            f"{self.base_url}{PAIRING_REGISTER_PATH}",
            json=request.model_dump(mode="json"),
        )

    async def poll(self, *, code: str, fingerprint: str) -> httpx.Response:
        """Submit one poll request."""
        return await self.http_client.get(
            f"{self.base_url}{PAIRING_POLL_PATH}",
            params={"code": code, "fingerprint": fingerprint},
        )

    @staticmethod
    def parse_poll_response(payload: object) -> PairingPollResponse:
        """Validate a poll payload received from the backend."""
        return PairingPollResponse.model_validate(payload)
