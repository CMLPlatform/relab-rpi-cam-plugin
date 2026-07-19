# ADR 0001: Remote preview streams as LL-HLS over the command relay

- Status: accepted
- Date: 2026-07-19
- Scope: plugin + Relab backend (`app/api/plugins/rpi_cam/`)

## Context

The Pi publishes its preview to a local MediaMTX sidecar; browsers consume it
as LL-HLS. Two paths exist:

- **Local direct** (fast path): a browser on the same network fetches
  `/preview/hls/...` straight from the Pi. Low latency, no backend involvement.
- **Remote via relay**: the backend proxies each HLS request through the
  device's WebSocket command tunnel (`GET /preview/hls/` is allowlisted in
  `relab_rpi_cam_models`). Every manifest and segment travels as a relay
  envelope; when the HTTP request lands on a different Uvicorn worker than the
  one holding the camera's socket, it additionally crosses the Redis
  cross-worker bridge base64-encoded.

The remote path is heavy per segment: JSON/WS framing, an in-process dispatch
on the Pi, backend fan-in, and up to ~33% base64 inflation across Redis. It is
also latency-bound by LL-HLS itself (a few seconds) and capped by the shared
`RELAY_COMMAND_TIMEOUT_SECONDS` per segment fetch.

## Decision

Keep LL-HLS over the command relay as the remote preview transport for now.
Do not invest further in optimizing this path (sticky worker routing,
binary-safe Redis clients, segment caching).

Why it holds:

- **Zero extra infrastructure.** It reuses the existing tunnel, allowlist,
  auth, and MediaMTX config. The modern alternative (WebRTC) requires a TURN
  server for NATed remote viewers.
- **Remote preview is a glance, not a workload.** Framing checks during
  documentation happen on the local network, where the direct path already
  serves low-latency LL-HLS. The preview thumbnail endpoint covers the remote
  "is the camera pointed right" case without streaming at all.
- **Cost is bounded.** `PreviewSleeper` hibernates the encoder when the relay
  is idle, and the relay's frame/queue/concurrency limits cap what a preview
  session can consume.

## Consequences

- Remote preview latency stays at LL-HLS levels (seconds, not sub-second).
- Backend CPU/bandwidth scales linearly with remote viewers × bitrate; the
  Redis bridge adds base64 overhead whenever worker affinity misses.
- A segment fetch slower than the shared relay command timeout fails; the
  player retries.

## Future: WebRTC (WHEP)

MediaMTX natively serves WHEP, so the migration path does not touch the
camera pipeline:

1. Allowlist one relay command for WHEP signaling (SDP offer/answer exchange
   with MediaMTX) — small JSON, a perfect fit for the existing tunnel.
2. Media then flows browser ↔ Pi directly: on-LAN via host candidates
   (no new infra), across NAT via STUN + a TURN relay (coturn) that must be
   deployed and paid for.
3. Delete the `GET /preview/hls/` prefix from the relay allowlist; keep
   local-direct LL-HLS or WHEP for the LAN case.

Revisit this ADR when any of these hold:

- Remote live preview becomes a routine part of documentation workflows
  rather than an occasional check.
- Users report preview latency as a problem (LL-HLS floor ≈ 2–5 s).
- Backend metrics show relay HLS traffic contributing meaningful bandwidth,
  CPU, or Redis load.
- More than one concurrent remote viewer per camera becomes normal (each
  viewer multiplies the per-segment relay cost; WebRTC would move that load
  off the backend entirely).

## Alternatives considered

- **Optimize HLS-over-relay** (sticky routing per camera, raw-bytes Redis
  bridge, manifest micro-cache): rejected — effort spent polishing a transport
  that WebRTC obsoletes, while keeping its latency floor.
- **General-purpose tunnels** (Tailscale, Cloudflare Tunnel, frp): rejected —
  they expose the whole local API and move the auth boundary out of the
  shared contract package; the 13-command allowlist is the security model.
