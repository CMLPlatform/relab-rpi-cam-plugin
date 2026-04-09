"""Tests for stream service helpers."""

from types import SimpleNamespace
from typing import Self

import pytest
from pydantic import AnyUrl, SecretStr
from relab_rpi_cam_models.stream import StreamMode

from app.api.schemas.streaming import YoutubeConfigRequiredError, YoutubeStreamConfig
from app.api.services import stream as stream_service

MANIFEST_NAME_KEY = "master_pl_name"
POST_METHOD_MARKER = "method POST"
UPLOAD_URL = "https://example.com/upload"
EMBED_URL = "https://www.youtube.com/embed/broadcast-key"


class DummyFfmpegOutput:
    """Capture FfmpegOutput construction arguments."""

    def __init__(self, output_str: str, **kwargs: object) -> None:
        self.output_str = output_str
        self.kwargs = kwargs


class DummyAsyncClient:
    """Minimal async client stub for stream key validation."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.requests: list[str] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def post(self, url: str) -> SimpleNamespace:
        """Capture POST requests and return a preset status code."""
        self.requests.append(url)
        return SimpleNamespace(status_code=self.status_code)


class TestStreamUrls:
    """Tests for stream URL helpers."""

    def test_upload_url_uses_manifest_name(self) -> None:
        """Test that the upload URL includes the manifest filename."""
        config = YoutubeStreamConfig(
            stream_key=SecretStr("stream-key"),
            broadcast_key=SecretStr("broadcast-key"),
        )
        url = stream_service.get_upload_url(config)
        assert url == AnyUrl(
            "https://a.upload.youtube.com/http_upload_hls?cid=stream-key&copy=0&file=master.m3u8",
        )

    def test_broadcast_url(self) -> None:
        """Test that the broadcast URL is constructed using the broadcast key from the config."""
        config = YoutubeStreamConfig(
            stream_key=SecretStr("stream-key"),
            broadcast_key=SecretStr("broadcast-key"),
        )
        assert stream_service.get_broadcast_url(config) == AnyUrl("https://youtube.com/watch?v=broadcast-key")

    def test_embed_url(self) -> None:
        """Test that a watch URL is converted into an embed URL."""
        assert stream_service.get_youtube_embed_url(AnyUrl("https://youtube.com/watch?v=broadcast-key")) == EMBED_URL


class TestFfmpegOutput:
    """Tests for FFmpeg output construction."""

    def test_youtube_output_requires_config(self) -> None:
        """Test that requesting a YouTube FFmpeg output without providing the necessary config raises an error."""
        with pytest.raises(YoutubeConfigRequiredError):
            stream_service.get_ffmpeg_output(StreamMode.YOUTUBE)

    def test_youtube_output_includes_stream_key_and_audio(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that the YouTube FFmpeg output is built using the config stream key and includes audio settings."""
        monkeypatch.setattr(stream_service, "FfmpegOutput", DummyFfmpegOutput)
        monkeypatch.setattr(stream_service, "get_upload_url", lambda _: "https://example.com/upload")
        config = YoutubeStreamConfig(
            stream_key=SecretStr("stream-key"),
            broadcast_key=SecretStr("broadcast-key"),
        )

        output = stream_service.get_ffmpeg_output(StreamMode.YOUTUBE, config)
        assert isinstance(output, DummyFfmpegOutput)
        assert output.kwargs["audio"] is True
        assert MANIFEST_NAME_KEY in output.output_str
        assert POST_METHOD_MARKER in output.output_str
        assert UPLOAD_URL in output.output_str


class TestValidateStreamKey:
    """Tests for stream key validation."""

    async def test_returns_true_for_accepted_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that if the validation endpoint returns a 2xx status code, the function returns True."""
        monkeypatch.setattr(stream_service.httpx, "AsyncClient", lambda **_: DummyAsyncClient(202))
        config = YoutubeStreamConfig(
            stream_key=SecretStr("stream-key"),
            broadcast_key=SecretStr("broadcast-key"),
        )
        assert await stream_service.validate_stream_key(config) is True

    async def test_returns_false_for_rejected_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that if the validation endpoint returns a non-2xx status code, the function returns False."""
        monkeypatch.setattr(stream_service.httpx, "AsyncClient", lambda **_: DummyAsyncClient(400))
        config = YoutubeStreamConfig(
            stream_key=SecretStr("stream-key"),
            broadcast_key=SecretStr("broadcast-key"),
        )
        assert await stream_service.validate_stream_key(config) is False
