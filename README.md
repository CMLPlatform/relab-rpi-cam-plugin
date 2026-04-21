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
- Publishes a low-latency LL-HLS preview through the local MediaMTX sidecar
- Connects to the RELab platform via WebSocket relay
- Exposes a REST API for manual testing and integration

Supports **Raspberry Pi 5/4** with **Camera Module 3/v2**, running on Raspberry Pi OS (64-bit) with Python 3.13+.

## Architecture

The app uses a **feature-first** layout. Each HTTP feature (`camera/`, `pairing/`, `auth/`, `system/`, `frontend/`) owns its router, schemas, dependencies, and services in one folder. Infra packages (`backend/`, `relay/`, `media/`, `upload/`, `image_sinks/`) live as peers at `app/` root. A small `AppRuntime` in `app/core/` owns the long-lived process services (camera manager, relay, pairing, preview pipeline, workers).

Config precedence: env-backed `Settings` → persisted credentials file (`~/.config/relab/relay_credentials.json`) → generated first-boot defaults. The device contract lives in the separately published `relab_rpi_cam_models` package.

See [CONTRIBUTING.md](CONTRIBUTING.md) for a deeper tour.

## Supported Modes

| Mode               | What it does                                          |
| ------------------ | ----------------------------------------------------- |
| **Paired relay**   | Outbound WebSocket relay to the RELab backend         |
| **Local direct**   | Ethernet/LAN access with `X-API-Key` auth             |
| **Backend upload** | Captures pushed back to the RELab backend             |
| **S3 upload**      | Captures written to a configured S3-compatible bucket |

## Getting Started

1. [Install the plugin](INSTALL.md) on your Pi.
1. Set `PAIRING_BACKEND_URL` in `.env` and start the plugin (`docker compose up -d` or `just dev`).
1. Enter the 6-character pairing code (shown on `/setup` or in the `PAIRING READY` log banner) in the RELab app.
1. Visit `http://your-pi-ip:8018` to check:
   - `/setup` — pairing and status
   - `/docs` — API reference (Swagger UI)
   - `/preview/hls/cam-preview/index.m3u8` — LL-HLS live preview (MediaMTX sidecar on `:8888`)
   - `/camera/controls` — discover/set autofocus, exposure, etc.
   - `/captures` — trigger a still capture

Need a fresh code? Click **Generate a new pairing code** on `/setup`. It rotates the code without unpairing the camera.

Docker device mappings: generate `compose.override.yml` with `./scripts/generate_compose_override.py`.

### Browser vs native RELab app

The native RELab app drives pairing end-to-end. In the HTTPS-served web frontend, browsers block plain-HTTP calls to the Pi as mixed content, so direct-mode auto-probing doesn't apply — use the native app or call the Pi manually.

### Observability (optional)

Ship structured logs to an external Loki-compatible collector via the `observability-ship` Compose profile:

```sh
COMPOSE_PROFILES=observability-ship
LOKI_PUSH_URL=http://your-observability-host:3100/loki/api/v1/push
OBSERVABILITY_INSTANCE=pi-01
```

Tracing is opt-in via `OTEL_ENABLED=true` + `OTEL_EXPORTER_OTLP_ENDPOINT`. Neither Loki/Grafana nor an OTLP collector is bundled — point at your central stack.

For platform-side operation, see the [RELab camera guide](https://docs.cml-relab.org/user-guides/rpi-cam/).

## Standalone mode (no RELab backend)

The plugin can also run fully standalone, writing captures straight to a local
S3-compatible bucket (RustFS by default) instead of pushing them to the RELab
backend. This is useful for hobbyist / bench / offline-first setups.

Everything lives in the same `compose.yml`. Standalone mode uses a separate
build target that includes the S3 client (`aioboto3`); the default paired build
does not. Set the following in `.env` and rebuild:

```sh
# Select the standalone build target and start the RustFS sidecar
APP_BUILD_TARGET=runtime-standalone
COMPOSE_PROFILES=standalone

# S3 sink and RustFS credentials — see .env.example for the full list
IMAGE_SINK=s3
S3_ENDPOINT_URL=http://host.docker.internal:9000
S3_BUCKET=rpi-cam
S3_ACCESS_KEY_ID=rustfsadmin
S3_SECRET_ACCESS_KEY=change-me-to-a-strong-password
RUSTFS_SECRET_KEY=change-me-to-a-strong-password
```

```sh
docker compose build && docker compose up -d
```

Once up:

- Camera API at `http://<pi-lan-ip>:8018` (same as paired mode)
- Live LL-HLS preview at the same URL shape as paired mode (proxied through
  the Pi's own `/hls` endpoint; no RELab backend needed)
- RustFS console at `http://<pi-lan-ip>:9001` — log in with
  `RUSTFS_ACCESS_KEY` / `RUSTFS_SECRET_KEY`
- Captures browsable under `http://<pi-lan-ip>:9000/rpi-cam/`

To point the plugin at an external S3-compatible service (Backblaze B2,
Cloudflare R2, Wasabi, AWS S3, …), update `S3_ENDPOINT_URL`, credentials, and
`S3_PUBLIC_URL_TEMPLATE` in `.env` and rebuild. No code changes required.
Set `COMPOSE_PROFILES=` (empty) to skip the RustFS sidecar when using a managed
bucket.

Profiles combine freely: `COMPOSE_PROFILES=standalone,observability-ship`
runs the RustFS sidecar *and* ships logs to your central Loki.

## Local (direct) connection mode

Local mode is **enabled by default**. On first boot the plugin generates a local API key and persists it. Two use-cases:

- **RELab app latency boost** (after pairing) — the app fetches the key through the relay and switches to Ethernet-direct on the same LAN, dropping preview latency from ~2 s to ~0.4 s. No user action.
- **Standalone / custom clients** (no pairing needed) — call the API directly with `X-API-Key: <key>`.

> The local key is a latency optimization for paired cameras and an auth gate for custom clients. It does **not** replace relay pairing for registering a camera in the RELab app.

Disable with `LOCAL_MODE_ENABLED=false`.

### Retrieving the local key

On startup the plugin logs a banner showing the current mode + how to fetch the key. From an SSH session:

```sh
just show-key
# or directly from the credentials file:
python3 -c "import json,pathlib; print(json.loads((pathlib.Path.home()/'.config/relab/relay_credentials.json').read_text()).get('local_api_key',''))"
```

### Discovery and hardware

- **mDNS (optional)**: install `avahi-daemon` and advertise `_relab-rpi-cam._tcp` on port 8018 to reach the Pi at `<hostname>.local`. See [INSTALL.md](INSTALL.md) for the service-file snippet.
- **Ethernet**: any port works on any RPi model; link-local (`169.254.x.x`) is negotiated automatically if no DHCP.
- **USB gadget mode** (USB-C data): only on RPi Zero 2W and some RPi 4 revisions — not RPi 5.
- **Contract**: any device implementing `GET /camera`, `POST /captures`, and `GET /preview/hls/*` with `X-API-Key` auth works with the RELab frontend's local mode.

The local API key is the only authentication gate on the direct interface; physical access to the cable is the trust boundary.

## Troubleshooting

See [INSTALL.md — Troubleshooting](INSTALL.md#troubleshooting). For development setup, see [CONTRIBUTING.md](CONTRIBUTING.md).
