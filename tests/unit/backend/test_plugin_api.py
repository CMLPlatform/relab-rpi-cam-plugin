"""Tests for the plugin API prefix and its one-release legacy fallback."""

from unittest.mock import MagicMock

import pytest

from app.backend.plugin_api import send_with_legacy_fallback


def _response(status: int) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    return resp


class _Recorder:
    def __init__(self, *statuses: int) -> None:
        self.paths: list[str] = []
        self._statuses = list(statuses)

    async def __call__(self, path: str) -> MagicMock:
        self.paths.append(path)
        return _response(self._statuses.pop(0))


class TestSendWithLegacyFallback:
    """The new prefix goes first; only a 404 triggers the legacy retry."""

    async def test_new_prefix_success_does_not_retry(self) -> None:
        """A success on the new prefix is returned as is."""
        send = _Recorder(200)
        response = await send_with_legacy_fallback(send, "/pairing/poll")
        assert response.status_code == 200
        assert send.paths == ["/v1/plugins/r9cam/pairing/poll"]

    async def test_404_retries_on_legacy_prefix(self) -> None:
        """A 404 on the new prefix is retried once on the legacy prefix."""
        send = _Recorder(404, 200)
        response = await send_with_legacy_fallback(send, "/pairing/poll")
        assert response.status_code == 200
        assert send.paths == ["/v1/plugins/r9cam/pairing/poll", "/v1/plugins/rpi-cam/pairing/poll"]

    @pytest.mark.parametrize("status", [401, 403, 500])
    async def test_other_errors_are_returned_without_retry(self, status: int) -> None:
        """Non-404 errors are returned without a second call."""
        send = _Recorder(status)
        response = await send_with_legacy_fallback(send, "/pairing/poll")
        assert response.status_code == status
        assert len(send.paths) == 1
