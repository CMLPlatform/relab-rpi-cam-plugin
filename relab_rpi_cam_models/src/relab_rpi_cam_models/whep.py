"""WHEP (WebRTC HTTP Egress) signalling envelopes shared between Pi, backend, and frontend."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

_EXAMPLE_WHEP_OFFER_SDP = (
    "v=0\r\n"
    "o=- 4611733059559855913 2 IN IP4 127.0.0.1\r\n"
    "s=-\r\n"
    "t=0 0\r\n"
    "a=group:BUNDLE 0\r\n"
    "a=msid-semantic: WMS\r\n"
    "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
    "c=IN IP4 0.0.0.0\r\n"
    "a=rtcp:9 IN IP4 0.0.0.0\r\n"
    "a=ice-ufrag:exampleUfrag\r\n"
    "a=ice-pwd:exampleIcePasswordForWhepOffer123\r\n"
    "a=fingerprint:sha-256 12:34:56:78:9A:BC:DE:F0:12:34:56:78:9A:BC:DE:F0:"
    "12:34:56:78:9A:BC:DE:F0:12:34:56:78:9A:BC:DE:F0\r\n"
    "a=setup:actpass\r\n"
    "a=mid:0\r\n"
    "a=sendrecv\r\n"
    "a=rtcp-mux\r\n"
    "a=rtpmap:96 H264/90000\r\n"
    "a=fmtp:96 packetization-mode=1;level-asymmetry-allowed=1;profile-level-id=42e01f\r\n"
)


class WhepOfferRequest(BaseModel):
    """JSON envelope for an incoming WHEP offer."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"sdp": _EXAMPLE_WHEP_OFFER_SDP}]})

    sdp: str = Field(description="Raw SDP offer from the browser.")


class WhepAnswerResponse(BaseModel):
    """JSON envelope for an outgoing WHEP answer + opaque session id."""

    sdp: str = Field(description="Raw SDP answer from MediaMTX.")
    session_id: str = Field(description="Opaque handle used to tear the session down.")
