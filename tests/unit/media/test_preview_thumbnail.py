"""Tests for cached preview-thumbnail maintenance."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock

from PIL import Image

from app.backend.client import BackendUploadError
from app.camera.services.manager import CameraManager
from app.relay.state import RelayRuntimeState
from app.workers.preview_thumbnail import PreviewThumbnailWorker

if TYPE_CHECKING:
    from pathlib import Path

    from _pytest.monkeypatch import MonkeyPatch

_FRESH_BYTES = b"fresh-bytes"
_OLD_BYTES = b"old-bytes"
_NEW_BYTES = b"new-bytes"
_ACTIVITY_BYTES = b"activity-bytes"
_DEFAULT_FILENAME = "preview-thumbnail.jpg"
_BACKEND_DOWN = "backend down"


class TestPreviewThumbnailWorker:
    """Tests for the runtime-owned preview-thumbnail cache worker."""

    async def test_refresh_once_skips_upload_when_explicitly_disabled(
        self,
        tmp_path: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """``upload=False`` bypasses the backend client (and thus the active-runtime lookup)."""
        capture_preview_thumbnail_jpeg = AsyncMock(return_value=_FRESH_BYTES)
        camera_manager = cast(
            "CameraManager",
            SimpleNamespace(capture_preview_thumbnail_jpeg=capture_preview_thumbnail_jpeg),
        )
        relay_state = cast(
            "RelayRuntimeState",
            SimpleNamespace(seconds_since_last_hls_activity=lambda: None),
        )
        upload_calls: list[bytes] = []

        async def _upload_preview_thumbnail(*, image_bytes: bytes, filename: str = _DEFAULT_FILENAME) -> None:
            del filename
            upload_calls.append(image_bytes)

        monkeypatch.setattr("app.workers.preview_thumbnail.upload_preview_thumbnail", _upload_preview_thumbnail)

        worker = PreviewThumbnailWorker(
            camera_manager=camera_manager,
            relay_state=relay_state,
            relay_enabled_getter=lambda: True,
            cache_dir=tmp_path,
        )

        refreshed = await worker.refresh_once(reason="preview-stop", upload=False)

        assert refreshed is True
        assert (tmp_path / "current.jpg").read_bytes() == _FRESH_BYTES
        assert upload_calls == []

    async def test_refresh_once_forwards_preview_encoder_running_flag(
        self,
        tmp_path: Path,
    ) -> None:
        """The worker must tell the capture call whether the encoder owns the lores buffer."""
        capture_preview_thumbnail_jpeg = AsyncMock(return_value=_FRESH_BYTES)
        camera_manager = cast(
            "CameraManager",
            SimpleNamespace(capture_preview_thumbnail_jpeg=capture_preview_thumbnail_jpeg),
        )
        relay_state = cast(
            "RelayRuntimeState",
            SimpleNamespace(seconds_since_last_hls_activity=lambda: None),
        )
        worker = PreviewThumbnailWorker(
            camera_manager=camera_manager,
            relay_state=relay_state,
            relay_enabled_getter=lambda: False,
            is_preview_running_getter=lambda: True,
            cache_dir=tmp_path,
        )

        await worker.refresh_once(reason="interval")

        await_args = capture_preview_thumbnail_jpeg.await_args
        assert await_args is not None
        assert await_args.kwargs["preview_encoder_running"] is True

    async def test_refresh_once_writes_local_cache_and_uploads_when_paired(
        self,
        tmp_path: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """A successful refresh should cache locally and push to the backend."""
        capture_preview_thumbnail_jpeg = AsyncMock(return_value=_FRESH_BYTES)
        camera_manager = cast(
            "CameraManager",
            SimpleNamespace(capture_preview_thumbnail_jpeg=capture_preview_thumbnail_jpeg),
        )
        relay_state = cast(
            "RelayRuntimeState",
            SimpleNamespace(seconds_since_last_hls_activity=lambda: None),
        )
        uploaded: list[bytes] = []

        async def _upload_preview_thumbnail(*, image_bytes: bytes, filename: str = _DEFAULT_FILENAME) -> None:
            uploaded.append(image_bytes)
            assert filename == _DEFAULT_FILENAME

        monkeypatch.setattr("app.workers.preview_thumbnail.upload_preview_thumbnail", _upload_preview_thumbnail)

        worker = PreviewThumbnailWorker(
            camera_manager=camera_manager,
            relay_state=relay_state,
            relay_enabled_getter=lambda: True,
            cache_dir=tmp_path,
        )

        refreshed = await worker.refresh_once(reason="startup")

        assert refreshed is True
        assert worker.cache_path.read_bytes() == _FRESH_BYTES
        assert uploaded == [_FRESH_BYTES]

    async def test_failed_refresh_keeps_previous_cached_thumbnail(
        self,
        tmp_path: Path,
    ) -> None:
        """A skipped refresh should leave the previous cached file untouched."""
        capture_preview_thumbnail_jpeg = AsyncMock(return_value=None)
        camera_manager = cast(
            "CameraManager",
            SimpleNamespace(capture_preview_thumbnail_jpeg=capture_preview_thumbnail_jpeg),
        )
        relay_state = cast(
            "RelayRuntimeState",
            SimpleNamespace(seconds_since_last_hls_activity=lambda: None),
        )
        worker = PreviewThumbnailWorker(
            camera_manager=camera_manager,
            relay_state=relay_state,
            relay_enabled_getter=lambda: False,
            cache_dir=tmp_path,
        )
        worker.cache_path.parent.mkdir(parents=True, exist_ok=True)
        worker.cache_path.write_bytes(_OLD_BYTES)

        refreshed = await worker.refresh_once(reason="interval")

        assert refreshed is False
        assert worker.cache_path.read_bytes() == _OLD_BYTES

    async def test_upload_failure_keeps_local_cache(
        self,
        tmp_path: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """An upload failure should not discard the freshly cached local thumbnail."""
        capture_preview_thumbnail_jpeg = AsyncMock(return_value=_NEW_BYTES)
        camera_manager = cast(
            "CameraManager",
            SimpleNamespace(capture_preview_thumbnail_jpeg=capture_preview_thumbnail_jpeg),
        )
        relay_state = cast(
            "RelayRuntimeState",
            SimpleNamespace(seconds_since_last_hls_activity=lambda: None),
        )

        async def _upload_preview_thumbnail(*, image_bytes: bytes, filename: str = _DEFAULT_FILENAME) -> None:
            del image_bytes, filename
            raise BackendUploadError(_BACKEND_DOWN)

        monkeypatch.setattr("app.workers.preview_thumbnail.upload_preview_thumbnail", _upload_preview_thumbnail)

        worker = PreviewThumbnailWorker(
            camera_manager=camera_manager,
            relay_state=relay_state,
            relay_enabled_getter=lambda: True,
            cache_dir=tmp_path,
        )

        refreshed = await worker.refresh_once(reason="interval")

        assert refreshed is True
        assert worker.cache_path.read_bytes() == _NEW_BYTES

    async def test_refresh_from_frame_encodes_caches_and_uploads(
        self,
        tmp_path: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """refresh_from_frame should encode the PIL frame, cache it, and upload it when paired."""
        capture_preview_thumbnail_jpeg = AsyncMock(return_value=None)
        camera_manager = cast(
            "CameraManager",
            SimpleNamespace(capture_preview_thumbnail_jpeg=capture_preview_thumbnail_jpeg),
        )
        relay_state = cast(
            "RelayRuntimeState",
            SimpleNamespace(seconds_since_last_hls_activity=lambda: None),
        )
        uploaded: list[bytes] = []

        async def _upload_preview_thumbnail(*, image_bytes: bytes, filename: str = _DEFAULT_FILENAME) -> None:
            uploaded.append(image_bytes)
            assert filename == _DEFAULT_FILENAME

        monkeypatch.setattr("app.workers.preview_thumbnail.upload_preview_thumbnail", _upload_preview_thumbnail)

        worker = PreviewThumbnailWorker(
            camera_manager=camera_manager,
            relay_state=relay_state,
            relay_enabled_getter=lambda: True,
            cache_dir=tmp_path,
        )

        frame = Image.new("RGB", (1280, 720), color="blue")
        refreshed = await worker.refresh_from_frame(frame)

        assert refreshed is True
        cached_bytes = worker.cache_path.read_bytes()
        assert cached_bytes.startswith(b"\xff\xd8")
        assert uploaded == [cached_bytes]
        capture_preview_thumbnail_jpeg.assert_not_awaited()

    async def test_activity_refresh_uses_recent_hls_activity(
        self,
        tmp_path: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """Recent HLS activity should trigger an opportunistic thumbnail refresh."""
        capture_preview_thumbnail_jpeg = AsyncMock(return_value=_ACTIVITY_BYTES)
        camera_manager = cast(
            "CameraManager",
            SimpleNamespace(capture_preview_thumbnail_jpeg=capture_preview_thumbnail_jpeg),
        )
        relay_state = cast(
            "RelayRuntimeState",
            SimpleNamespace(seconds_since_last_hls_activity=lambda: 0.0),
        )

        async def _upload_preview_thumbnail(*, image_bytes: bytes, filename: str = _DEFAULT_FILENAME) -> None:
            del image_bytes, filename

        monkeypatch.setattr("app.workers.preview_thumbnail.upload_preview_thumbnail", _upload_preview_thumbnail)

        now = 1_000.0
        worker = PreviewThumbnailWorker(
            camera_manager=camera_manager,
            relay_state=relay_state,
            relay_enabled_getter=lambda: True,
            cache_dir=tmp_path,
            monotonic=lambda: now,
        )
        worker._last_refresh_monotonic = now - 601.0

        await worker._maybe_refresh()

        capture_preview_thumbnail_jpeg.assert_awaited_once()
        assert worker.cache_path.read_bytes() == _ACTIVITY_BYTES
