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

A small runtime container (`AppRuntime`) owns the long-lived process services:

- **FastAPI control plane** — HTTP routes, setup UI, auth, local-access helpers
- **Camera + media path** — Picamera2 capture, lores encoder → MediaMTX → LL-HLS proxy, upload fallback queue
- **Relay + pairing path** — runtime-owned `PairingService` and `RelayService`, local-access bootstrap
- **Background services** — upload queue drain, preview sleeper, thermal governor, stream health checks

Static config comes from `app.core.config.Settings`; live mutable state lives in `app.core.runtime_state.RuntimeState`. Bootstrap precedence is env `Settings` → persisted credentials file → generated first-boot defaults. The device contract (`relab_rpi_cam_models`) is a separately published PyPI package; the uv workspace link in this repo is a dev convenience only.

See [CONTRIBUTING.md](CONTRIBUTING.md) for a deeper tour of the module layout and request flow.

## Supported Modes

- **Paired relay mode**: outbound WebSocket relay to the RELab backend
- **Local direct mode**: Ethernet/LAN access with `X-API-Key` auth
- **Backend upload mode**: captures pushed back to the RELab backend
- **S3 upload mode**: captures written to a configured S3-compatible bucket

## Connection

The plugin connects to the RELab backend via **WebSocket relay**. The Pi opens an outbound connection to the backend — no public IP address or port forwarding is required.

Pairing is automatic in the native RELab app: set `PAIRING_BACKEND_URL` in your `.env`, start the plugin, and enter the 6-character code in the RELab app. The Pi generates an asymmetric key pair locally, registers the public key with the backend, and keeps the private key on-device. No API key is ever copied manually.

The browser-based RELab frontend is different: if it is served over HTTPS, modern browsers block `fetch()` calls to the Pi's plain HTTP API as mixed content. That means the web frontend cannot auto-probe or switch a camera into direct mode; this direct-connection path is for the native app or for manual clients that call the Pi directly.

## Getting Started

1. [Prepare your Pi and install the plugin](INSTALL.md) — see [Step 2](INSTALL.md#step-2-configure-connection) for full `.env` and configuration precedence
1. Set `PAIRING_BACKEND_URL` in your `.env` and start the plugin
1. Enter the pairing code shown on `/setup` (or in logs) in the RELab app
1. Visit `http://your-pi-ip:8018` to test
   - `/setup` — Pairing and status
   - `/preview/hls/cam-preview/index.m3u8` — LL-HLS live preview (via the MediaMTX sidecar on :8888)
   - `/camera/controls` — Discover/set camera controls (autofocus, exposure, etc.)
   - `/camera/focus` — Friendly focus controls (continuous/auto/manual)
   - `/captures` — Trigger a still capture that uploads to the backend
   - `/docs` — API reference

If you use Docker Compose on the Pi, generate `compose.override.yml` with `./scripts/generate_compose_override.py`. The override targets the existing `app` service from `compose.yml`, so the device mappings merge into the plugin container cleanly.

For headless setup, the active 6-character pairing code is also printed to stdout in a boxed `PAIRING READY` banner, so you can read it over SSH, `docker compose logs`, or `journalctl` without opening the browser UI.

If you need a fresh code during setup, open `/setup` and click the `Generate a new pairing code` button beside the current code. That rotates the code without unpairing the camera.

By default, Docker Compose runs only the camera plugin. Inspect logs with:

```sh
docker compose logs -f app
```

Optional remote observability is available with the `observability-ship` profile. It runs Alloy on the Pi and ships the app's structured file logs to an external Loki-compatible endpoint. Add it to `COMPOSE_PROFILES` in your `.env`:

```sh
COMPOSE_PROFILES=observability-ship
LOKI_PUSH_URL=http://your-observability-host:3100/loki/api/v1/push
OBSERVABILITY_INSTANCE=pi-01
```

Without the profile, logs are still written to Docker logs and the 7-day rotating `app_logs` volume. Local Loki/Grafana is not bundled with this plugin; use your platform's central observability stack when you need fleet log browsing.

Tracing is opt-in. When `OTEL_ENABLED=true` and `OTEL_EXPORTER_OTLP_ENDPOINT`
are set, the plugin instruments FastAPI and httpx and propagates request/relay
trace context across the backend -> plugin boundary. This repo does not bundle
or configure a full OTLP pipeline; that remains an environment concern.

For platform management and operation, see the [RELab camera guide](https://docs.cml-relab.org/user-guides/rpi-cam/).

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

The local API key serves **two independent use-cases**:

| Use-case                        | Requires relay pairing? | What it does                                                                                                                                                                                                                                                              |
| ------------------------------- | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **RELab app latency boost**     | Yes                     | After relay pairing, the app fetches the key automatically through the relay and switches to Ethernet-direct when the Pi is on the same LAN — preview latency drops from ~2 s to ~0.4 s. No manual setup. Works in both the native app and modern browsers (Chrome/Edge). |
| **Standalone / custom clients** | No                      | Call the camera API directly with `X-API-Key: <key>` — no relay needed. Useful for scripts, custom dashboards, or standalone mode (see below).                                                                                                                            |

> **The local key does not replace relay pairing.** To register a camera in the RELab app you still need to complete the relay pairing flow (6-character code). The local key is for latency improvement once already paired, or for non-RELab access.

Optional: mDNS/Avahi discovery lets you reach the Pi at `<hostname>.local` instead of its IP — see [INSTALL.md — Zero-config discovery](INSTALL.md#zero-config-discovery-with-avahi-mdns-optional).

### Setup — zero configuration required

Local mode is **enabled by default**. No `.env` changes are needed.

On first startup the plugin auto-generates a local API key and persists it to the credentials file. When the RELab app opens the camera detail screen and the camera is online, it automatically retrieves the key and candidate IP addresses through the relay and probes your local network. If the Pi is reachable via Ethernet, the app switches to direct mode silently — preview latency drops to ~0.4–0.8 s without any user action.

This works in both the native app (iOS/Android) and modern Chromium-based browsers. The Pi sends `Access-Control-Allow-Private-Network: true` on all responses so Chrome's Private Network Access policy is satisfied as enforcement ramps up.

To disable local mode entirely (opt-out):

```sh
LOCAL_MODE_ENABLED=false
```

### Headless / SSH access

On startup the plugin logs a banner showing the current mode and how to retrieve the local API key:

```text
══════════════════════════════════════════════════════
  ReLab RPi Camera  v1.x
  Setup    : http://my-pi.local:8018/setup
  Mode     : PAIRED      camera_id=…
  Local key: run:  just show-key
══════════════════════════════════════════════════════
```

To print the key from an SSH session:

```sh
just show-key
# or without just, if the app is already running:
python3 -c "import json,pathlib; print(json.loads((pathlib.Path.home()/'.config/relab/relay_credentials.json').read_text()).get('local_api_key',''))"
```

### Zero-config discovery with Avahi (mDNS, optional)

The RELab app discovers the Pi's IP addresses automatically via the relay. Avahi is not required, but it lets you access `/setup` and the API by hostname instead of IP on the local link:

```sh
sudo apt install avahi-daemon avahi-utils
```

Create a service advertisement file:

```xml
<!-- /etc/avahi/services/relab-rpi-cam.service -->
<service-group>
  <name>relab-rpi-cam-%h</name>
  <service>
    <type>_relab-rpi-cam._tcp</type>
    <port>8018</port>
  </service>
</service-group>
```

```sh
sudo systemctl enable avahi-daemon && sudo systemctl restart avahi-daemon
```

After this, the Pi is resolvable as `<hostname>.local` on macOS and Windows 10+ without any DNS configuration.

### Hardware notes

- **Any Ethernet port** — Works on all RPi models and any Linux SBC. Connect directly with a cable or through a USB-C to Ethernet adapter. Link-local addressing (169.254.x.x) is negotiated automatically if no DHCP is present.
- **USB gadget mode (USB-C data)** — Only available on RPi Zero 2W and some RPi 4 revisions (not RPi 5). Requires `dtoverlay=dwc2` + `g_ether` module; Pi appears at `192.168.7.1` on the host.
- **Hardware-agnostic contract** — Any camera device that implements `GET /camera`, `POST /captures`, and `GET /preview/hls/*` with `X-API-Key` authentication works with the RELab frontend's local connection mode.

### Security

The local API key is the only authentication gate on the direct interface. Physical access to the cable is the primary trust boundary — appropriate for lab use. The key is distinct from relay credentials; disable local auth with `LOCAL_MODE_ENABLED=false` if needed.

## Troubleshooting

**Camera not detected?** Run `rpicam-hello --list-cameras`

**Won't connect?** See [INSTALL.md — Troubleshooting](INSTALL.md#troubleshooting) for connection-specific issues.

**Want to contribute?** See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup.
