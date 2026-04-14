"""Tests for the WHEP proxy router logic (unit level).

Full end-to-end WHEP testing requires a real MediaMTX; these tests mock the
httpx client and the preview pipeline manager to validate the proxy
mechanics: SDP forwarding, Location header handling, session bookkeeping,
and refcount management on failure paths.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException
from relab_rpi_cam_models.whep import WhepOfferRequest

from app.api.exceptions import CameraInitializationError
from app.api.routers import whep as whep_mod
from app.api.routers.whep import _post_offer_to_mediamtx
from tests.constants import (
    ANSWER_SDP,
    EXTERNAL_LOCATION,
    HTTP_400_TEXT,
    LOCATION_FULL,
    LOCATION_HEADER_TEXT,
    MEDIA_UNREACHABLE,
    SDP_FINGERPRINT,
    SDP_ICE_LABEL,
    SDP_ICE_UFRAG,
    SDP_MID,
    SDP_RTPMAP,
)

if TYPE_CHECKING:
    from contextlib import AbstractContextManager


class _Response:
    """Minimal stand-in for an httpx Response."""

    def __init__(self, status_code: int, body: str = "", headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.text = body
        self.headers = headers or {}


def _patch_httpx(response: _Response | Exception | list[_Response]) -> AbstractContextManager[object]:
    client = MagicMock()
    if isinstance(response, (Exception, list)):
        client.post = AsyncMock(side_effect=response)
    else:
        client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return patch.object(whep_mod.httpx, "AsyncClient", return_value=client)


class TestPostOfferToMediamtx:
    """Tests for the raw SDP forwarder used by the WHEP router."""

    def test_request_schema_example_has_required_webrtc_lines(self) -> None:
        """The OpenAPI example should look like a real browser WHEP offer."""
        example = WhepOfferRequest.model_json_schema()["examples"][0]["sdp"]
        assert SDP_MID in example
        assert SDP_ICE_UFRAG in example
        assert SDP_ICE_LABEL in example
        assert SDP_FINGERPRINT in example
        assert SDP_RTPMAP in example

    async def test_happy_path_returns_answer_and_absolute_location(self) -> None:
        """MediaMTX returns a 201 with answer SDP and a relative Location."""
        response = _Response(
            201,
            body="v=0\no=- 1 1 IN IP4 192.168.1.2\n",
            headers={"Location": "/cam-preview/whep/abc"},
        )
        with _patch_httpx(response):
            answer, location = await _post_offer_to_mediamtx("v=0\no=- offer")

        assert answer.startswith("v=0")
        assert location == LOCATION_FULL

    async def test_absolute_location_passes_through(self) -> None:
        """An absolute Location header should be returned verbatim."""
        response = _Response(
            201,
            body="v=0\n",
            headers={"Location": "http://external.example/cam-preview/whep/xyz"},
        )
        with _patch_httpx(response):
            _, location = await _post_offer_to_mediamtx("v=0\no=- offer")
        assert location == EXTERNAL_LOCATION

    async def test_missing_location_raises_502(self) -> None:
        """A 2xx without a Location header is a protocol error from MediaMTX."""
        response = _Response(201, body="v=0\n", headers={})
        with _patch_httpx(response), pytest.raises(HTTPException) as excinfo:
            await _post_offer_to_mediamtx("v=0\no=- offer")
        assert excinfo.value.status_code == 502
        assert LOCATION_HEADER_TEXT in str(excinfo.value.detail)

    async def test_mediamtx_4xx_raises_502(self) -> None:
        """A 4xx from MediaMTX surfaces as a 502 to the relay caller."""
        response = _Response(400, body="bad offer", headers={})
        with _patch_httpx(response), pytest.raises(HTTPException) as excinfo:
            await _post_offer_to_mediamtx("v=0\no=- offer")
        assert excinfo.value.status_code == 502
        assert HTTP_400_TEXT in str(excinfo.value.detail)

    async def test_mediamtx_no_stream_404_retries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A just-started RTSP publisher can appear shortly after the first WHEP offer."""
        sleep_mock = AsyncMock()
        monkeypatch.setattr(whep_mod.asyncio, "sleep", sleep_mock)
        responses = [
            _Response(404, body='{"error":"no stream is available on path \'cam-preview\'"}'),
            _Response(201, body="v=0\nanswer", headers={"Location": "/cam-preview/whep/abc"}),
        ]

        with _patch_httpx(responses):
            answer, location = await _post_offer_to_mediamtx("v=0\no=- offer")

        assert answer == ANSWER_SDP
        assert location == LOCATION_FULL
        sleep_mock.assert_awaited_once_with(0.25)

    async def test_network_error_raises_503(self) -> None:
        """A connection failure should surface as 503."""
        with _patch_httpx(httpx.ConnectError("refused")), pytest.raises(HTTPException) as excinfo:
            await _post_offer_to_mediamtx("v=0\no=- offer")
        assert excinfo.value.status_code == 503
        assert MEDIA_UNREACHABLE in str(excinfo.value.detail)


def _make_camera_manager(*, primed: bool = True) -> MagicMock:
    """Build a camera_manager mock whose setup_camera primes backend.camera."""
    camera_manager = MagicMock()
    camera = MagicMock() if primed else None
    camera_manager.backend.camera = camera

    async def _setup(_mode: object) -> None:
        camera_manager.backend.camera = camera

    camera_manager.setup_camera = AsyncMock(side_effect=_setup)
    return camera_manager


class TestOpenCloseSession:
    """Reference-counted lifecycle around the preview pipeline manager."""

    @pytest.fixture(autouse=True)
    def _reset_sessions(self) -> None:
        whep_mod.reset_sessions_for_tests()

    async def test_open_acquires_pipeline_and_stores_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A successful WHEP offer should acquire the pipeline and record the session id."""
        pipeline = MagicMock()
        pipeline.acquire = AsyncMock()
        pipeline.release = AsyncMock()
        pipeline.active_subscribers = 1
        monkeypatch.setattr(whep_mod, "get_preview_pipeline_manager", lambda: pipeline)

        async def _fake_post(_offer_sdp: str) -> tuple[str, str]:
            return (ANSWER_SDP, LOCATION_FULL)

        monkeypatch.setattr(whep_mod, "_post_offer_to_mediamtx", _fake_post)

        camera_manager = _make_camera_manager()

        response = await whep_mod.open_whep_session(
            camera_manager=camera_manager,
            offer=WhepOfferRequest(sdp="v=0\noffer"),
        )

        assert response.sdp == ANSWER_SDP
        assert len(response.session_id) == 32
        assert response.session_id in whep_mod._sessions
        pipeline.acquire.assert_awaited_once()
        pipeline.release.assert_not_called()

    async def test_open_rolls_back_pipeline_on_mediamtx_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If MediaMTX rejects the offer, the pipeline refcount must be released."""
        pipeline = MagicMock()
        pipeline.acquire = AsyncMock()
        pipeline.release = AsyncMock()
        monkeypatch.setattr(whep_mod, "get_preview_pipeline_manager", lambda: pipeline)

        async def _broken_post(_offer_sdp: str) -> tuple[str, str]:
            raise HTTPException(status_code=502, detail="MediaMTX said no")

        monkeypatch.setattr(whep_mod, "_post_offer_to_mediamtx", _broken_post)

        camera_manager = _make_camera_manager()

        with pytest.raises(HTTPException):
            await whep_mod.open_whep_session(
                camera_manager=camera_manager,
                offer=WhepOfferRequest(sdp="v=0\noffer"),
            )
        pipeline.release.assert_awaited_once()

    async def test_open_returns_503_when_pipeline_start_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A RuntimeError from pipeline.acquire surfaces as a 503."""
        pipeline = MagicMock()
        pipeline.acquire = AsyncMock(side_effect=RuntimeError("ffmpeg crashed"))
        monkeypatch.setattr(whep_mod, "get_preview_pipeline_manager", lambda: pipeline)

        camera_manager = _make_camera_manager()

        with pytest.raises(HTTPException) as excinfo:
            await whep_mod.open_whep_session(
                camera_manager=camera_manager,
                offer=WhepOfferRequest(sdp="v=0\noffer"),
            )
        assert excinfo.value.status_code == 503

    async def test_open_returns_503_when_camera_init_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If priming the persistent pipeline fails, WHEP should surface a 503."""
        pipeline = MagicMock()
        pipeline.acquire = AsyncMock()
        monkeypatch.setattr(whep_mod, "get_preview_pipeline_manager", lambda: pipeline)

        camera_manager = MagicMock()
        camera_manager.setup_camera = AsyncMock(side_effect=CameraInitializationError(0, "no camera attached"))

        with pytest.raises(HTTPException) as excinfo:
            await whep_mod.open_whep_session(
                camera_manager=camera_manager,
                offer=WhepOfferRequest(sdp="v=0\noffer"),
            )
        assert excinfo.value.status_code == 503
        pipeline.acquire.assert_not_called()

    async def test_close_deletes_session_and_releases_pipeline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Closing a known session should DELETE on MediaMTX and release the pipeline."""
        pipeline = MagicMock()
        pipeline.release = AsyncMock()
        pipeline.active_subscribers = 0
        monkeypatch.setattr(whep_mod, "get_preview_pipeline_manager", lambda: pipeline)

        delete_mock = AsyncMock()
        monkeypatch.setattr(whep_mod, "_delete_mediamtx_session", delete_mock)

        session_id = "a" * 32
        whep_mod._sessions[session_id] = LOCATION_FULL

        camera_manager = _make_camera_manager()

        await whep_mod.close_whep_session(
            camera_manager=camera_manager,
            session_id=session_id,
        )

        delete_mock.assert_awaited_once_with(LOCATION_FULL)
        pipeline.release.assert_awaited_once()
        assert session_id not in whep_mod._sessions

    async def test_close_unknown_session_returns_404(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DELETE on an unknown session id must not touch the pipeline."""
        pipeline = MagicMock()
        pipeline.release = AsyncMock()
        monkeypatch.setattr(whep_mod, "get_preview_pipeline_manager", lambda: pipeline)

        camera_manager = _make_camera_manager()

        with pytest.raises(HTTPException) as excinfo:
            await whep_mod.close_whep_session(
                camera_manager=camera_manager,
                session_id="b" * 32,
            )
        assert excinfo.value.status_code == 404
        pipeline.release.assert_not_called()

    async def test_close_releases_pipeline_even_if_mediamtx_delete_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A failed MediaMTX DELETE must still release the refcount to avoid leaks."""
        pipeline = MagicMock()
        pipeline.release = AsyncMock()
        monkeypatch.setattr(whep_mod, "get_preview_pipeline_manager", lambda: pipeline)

        async def _raise(_location: str) -> None:
            msg = "network went away"
            raise RuntimeError(msg)

        monkeypatch.setattr(whep_mod, "_delete_mediamtx_session", _raise)

        session_id = "c" * 32
        whep_mod._sessions[session_id] = EXTERNAL_LOCATION

        camera_manager = _make_camera_manager()

        with pytest.raises(RuntimeError):
            await whep_mod.close_whep_session(
                camera_manager=camera_manager,
                session_id=session_id,
            )
        pipeline.release.assert_awaited_once()
