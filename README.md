# RPI Camera Plugin

[![CI](https://github.com/CMLPlatform/relab-rpi-cam-plugin/actions/workflows/ci.yml/badge.svg)](https://github.com/CMLPlatform/relab-rpi-cam-plugin/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/CMLPlatform/relab-rpi-cam-plugin/graph/badge.svg)](https://codecov.io/gh/CMLPlatform/relab-rpi-cam-plugin)

Device-side software for automated image capture on Raspberry Pi, integrated with the [Reverse Engineering Lab platform](https://cml-relab.org).

## Quick Links

- **[Installation Guide](INSTALL.md)** — Hardware, setup, and configuration
- **[Contributing](CONTRIBUTING.md)** — Development workflow and testing
- **[Platform Docs](https://docs.cml-relab.org/user-guides/rpi-cam/)** — Camera management in RELab

## What It Does

The plugin runs a lightweight FastAPI server on your Raspberry Pi that:

- Captures images from the connected camera module
- Exposes low-resolution snapshot previews for live viewfinder polling
- Connects to the RELab platform via WebSocket relay
- Exposes a REST API for manual testing and integration

Supports **Raspberry Pi 5/4** with **Camera Module 3/v2**, running on Raspberry Pi OS (64-bit) with Python 3.13+.

## Connection

The plugin connects to the RELab backend via **WebSocket relay**. The Pi opens an outbound connection to the backend — no public IP address or port forwarding is required.

Pairing is automatic: set `PAIRING_BACKEND_URL` in your `.env`, start the plugin, and enter the 6-character code in the RELab app. The Pi generates an asymmetric key pair locally, registers the public key with the backend, and keeps the private key on-device. No API key is ever copied manually.

## Getting Started

1. [Prepare your Pi and install the plugin](INSTALL.md)
1. Set `PAIRING_BACKEND_URL` in your `.env` and start the plugin
1. Enter the pairing code shown on `/setup` (or in logs) in the RELab app
1. Visit `http://your-pi-ip:8018` to test
   - `/setup` — Pairing and status
   - `/hls/cam-preview/index.m3u8` — LL-HLS live preview (via the MediaMTX sidecar on :8888)
   - `/stream/watch` — YouTube viewer UI for an active YouTube stream
   - `/camera/controls` — Discover/set camera controls (autofocus, exposure, etc.)
   - `/camera/focus` — Friendly focus controls (continuous/auto/manual)
   - `/docs` — API reference

If you use Docker Compose on the Pi, generate `compose.override.yml` with `./scripts/generate_compose_override.py`. The override targets the existing `app` service from `compose.yml`, so the device mappings merge into the plugin container cleanly.

For headless setup, the active 6-character pairing code is also printed to stdout in a boxed `PAIRING READY` banner, so you can read it over SSH, `docker compose logs`, or `journalctl` without opening the browser UI.

By default, Docker Compose runs only the camera plugin. Inspect logs with:

```sh
docker compose logs -f app
```

Optional remote observability is available with the `observability-ship` profile. It runs Alloy on the Pi and ships the app's structured file logs to an external Loki-compatible endpoint:

```sh
export LOKI_PUSH_URL=http://your-observability-host:3100/loki/api/v1/push
export OBSERVABILITY_INSTANCE=pi-01
docker compose --profile observability-ship up -d
```

Without the profile, logs are still written to Docker logs and the 7-day rotating `app_logs` volume. Local Loki/Grafana is not bundled with this plugin; use your platform's central observability stack when you need fleet log browsing.

For platform management and operation, see the [RELab camera guide](https://docs.cml-relab.org/user-guides/rpi-cam/).

## Standalone mode (no RELab backend)

The plugin can also run fully standalone, writing captures straight to a local
S3-compatible bucket (RustFS by default) instead of pushing them to the RELab
backend. This is useful for hobbyist / bench / offline-first setups.

Everything lives in the same `compose.yml` — switch modes with a profile
flag, not a separate file.

```sh
# 1. Fill in the standalone section of .env (IMAGE_SINK=s3, S3_*, RUSTFS_*).
#    See .env.example for the full list of variables.
# 2. Start the stack with the standalone profile:
docker compose --profile standalone up -d
```

Once up:

- Camera API at `http://<pi-lan-ip>:8018` (same as paired mode)
- Live LL-HLS preview at the same URL shape as paired mode (proxied through
  the Pi's own `/hls` endpoint; no RELab backend needed)
- RustFS console at `http://<pi-lan-ip>:9001` — log in with
  `RUSTFS_ACCESS_KEY` / `RUSTFS_SECRET_KEY`
- Captures browsable under `http://<pi-lan-ip>:9000/rpi-cam/`

The single Docker image ships with `aioboto3` pre-installed so the same
artifact runs both paired and standalone — the `S3CompatibleSink` is
lazy-imported and never loads unless `IMAGE_SINK=s3`. Retention policies,
access logs, and lifecycle rules live on the RustFS bucket itself.

To point the plugin at a different S3-compatible service (Backblaze B2,
Cloudflare R2, Wasabi, AWS S3, …), update `S3_ENDPOINT_URL`, credentials, and
`S3_PUBLIC_URL_TEMPLATE` in `.env` and restart. No code changes. If you're
running against a managed bucket (i.e. not the bundled RustFS), you can skip
the `standalone` profile entirely and just run `docker compose up -d` — the
plugin will still use the S3 sink based on your env vars.

Profiles combine freely: `docker compose --profile standalone --profile observability-ship up -d`
runs the RustFS sidecar *and* ships logs to your central Loki.

## Troubleshooting

**Camera not detected?** Run `rpicam-hello --list-cameras`

**Won't connect?** See [INSTALL.md — Troubleshooting](INSTALL.md#troubleshooting) for connection-specific issues.

**Want to contribute?** See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup.
