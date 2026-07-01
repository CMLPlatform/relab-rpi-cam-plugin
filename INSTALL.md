# Installation And Setup Guide

This guide is for operators installing and running the RPI Camera Plugin on a Raspberry Pi.

## Requirements

### Hardware

- Raspberry Pi 5 or Raspberry Pi 4
- Raspberry Pi Camera Module 3 or v2
- MicroSD card, 8 GB or larger
- Power supply
- Ethernet or WiFi network
- Camera mount

### Software

- Raspberry Pi OS 64-bit
- Python 3.13+
- Docker Compose for the recommended runtime
- Network access to the RELab backend for paired mode

## Prepare The Pi

1. Install Raspberry Pi OS using the [official Raspberry Pi guide](https://www.raspberrypi.com/documentation/computers/getting-started.html#installing-the-operating-system).

1. Connect the camera module using the [camera module guide](https://www.raspberrypi.com/documentation/accessories/camera.html#connect-the-camera).

1. Confirm the camera is visible:

   ```sh
   rpicam-hello
   ```

1. Clone the plugin:

   ```sh
   git clone https://github.com/CMLPlatform/relab-rpi-cam-plugin.git
   cd relab-rpi-cam-plugin
   cp .env.example .env
   ```

## Paired RELab Mode

Paired mode connects the Pi to RELab through an outbound WebSocket relay. The Pi does not need a public IP address or inbound port forwarding.

Set the pairing backend URL in `.env`:

```sh
PAIRING_BACKEND_URL=https://api.cml-relab.org
```

Start the plugin with Docker Compose:

```sh
./scripts/generate_compose_override.py > compose.override.yml
docker compose build
docker compose up -d
```

Review `compose.override.yml` before startup. It should contain only current camera device nodes such as `/dev/media*`, `/dev/video*`, `/dev/v4l-subdev*`, and `/dev/dma_heap`. Regenerate it after kernel, camera, or hardware changes; do not use `privileged: true`.

View logs:

```sh
docker compose logs -f app
```

### Pair The Camera

When pairing mode is active, read the code from either surface:

- browser UI: `http://your-pi-ip:8018/setup`
- logs: the `PAIRING READY` banner

Treat `/setup` and pairing logs as local/operator-only during pairing. The code is a short-lived bootstrap credential; anyone who can reach either surface during its 10-minute window can try to claim the camera.

The setup page polls `GET /pairing/state` so it can reload when pairing or unpairing completes. That endpoint is intentionally public and returns only low-detail state (`status` and `relay_enabled`); it does not expose the pairing code, relay credentials, local API key, backend URLs, or camera IDs.

Enter the code in the native RELab app under Cameras > Add Camera. The Pi receives relay credentials, saves them to `~/.config/relab/relay_credentials.json`, and connects to the backend.

To rotate the code without deleting relay credentials, use **Generate a new pairing code** on `/setup`.
To rotate relay credentials, unpair the camera and pair it again. Unpairing clears the stored relay key first, and the next pairing creates a fresh key pair.

Docker Compose stores runtime credentials in a named volume mounted at `/home/rpicam/.config/relab`, so paired credentials survive container restarts.

The relay signing key is stored in `relay_credentials.json` with `0o600` permissions. Against SD-card theft, use full-disk encryption (LUKS); to keep the key off disk entirely, skip pairing and inject `RELAY_PRIVATE_KEY_PEM` (with `RELAY_CAMERA_ID`, `RELAY_KEY_ID`, `RELAY_AUTH_SCHEME`) via env.

The HTTPS-served RELab web frontend cannot auto-probe the Pi's plain-HTTP local API because browsers block mixed content. Use the native app for pairing and direct-mode setup.

## Standalone Mode

Standalone mode stores captures in an S3-compatible bucket instead of the RELab backend. The bundled standalone profile starts a local RustFS sidecar.

Set these values in `.env`:

```sh
APP_BUILD_TARGET=runtime-standalone
COMPOSE_PROFILES=standalone
APP_ENV=development

IMAGE_SINK=s3
S3_ENDPOINT_URL=http://127.0.0.1:9000
S3_BUCKET=rpi-cam
S3_ACCESS_KEY_ID=rustfsadmin
S3_SECRET_ACCESS_KEY=change-me-to-a-strong-password
RUSTFS_SECRET_KEY=change-me-to-a-strong-password
```

Start the stack:

```sh
docker compose build
docker compose up -d
```

Runtime surfaces:

- Camera API: `http://<pi-lan-ip>:8018`
- Setup UI: `http://<pi-lan-ip>:8018/setup`
- RustFS console: `http://127.0.0.1:9001`
- Captures: `http://127.0.0.1:9000/rpi-cam/`

RustFS is loopback-only by default. To expose it on the operator LAN, set `RUSTFS_API_BIND` and `RUSTFS_CONSOLE_BIND` to the Pi's LAN IP, then restrict ports `9000` and `9001` to trusted clients.

For an external S3-compatible service such as Backblaze B2, Cloudflare R2, Wasabi, or AWS S3, set `S3_ENDPOINT_URL`, credentials, and `S3_PUBLIC_URL_TEMPLATE`. Remote production S3 endpoints and remote public URL templates require HTTPS. Keep `APP_ENV=development` for local HTTP storage only.

Treat captures as sensitive device data. Current captures are always `.jpg` / `image/jpeg`, are bounded before upload or queueing, and are checked again before retry or serving. Failed uploads can stay in the local retry queue or dead-letter directory until the queue limits prune them. `S3_PUBLIC_URL_TEMPLATE` makes images as public as the bucket or proxy behind it, so use private or authenticated storage when needed.

If you rotate S3 or RustFS credentials, update the storage service first, then refresh `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, and `RUSTFS_SECRET_KEY` if needed.

Set `COMPOSE_PROFILES=` to skip the RustFS sidecar when using a managed bucket.

## Local Direct Mode

Local direct mode is enabled by default. On first boot, the plugin generates and persists a local API key. Local clients call the Pi API with:

```sh
X-API-Key: <local-api-key>
```

Paired RELab apps can fetch the key through the relay and switch to LAN/Ethernet direct access for lower preview latency. Custom clients can use the key without pairing.

Retrieve the key from an SSH session:

```sh
just show-key
```

Or read it directly from the credentials file:

```sh
python3 -c "import json,pathlib; print(json.loads((pathlib.Path.home()/'.config/relab/relay_credentials.json').read_text()).get('local_api_key',''))"
```

Disable direct local API access with:

```sh
LOCAL_MODE_ENABLED=false
```

To rotate the local API key, stop the app, remove `local_api_key` from `~/.config/relab/relay_credentials.json` or delete the credentials file when you re-pair, then start the app again. Custom clients using the old key will need the new one.

Transport security:

- The local API is plain **HTTP** by design — the API key and setup data cross the LAN unencrypted, so keep port `8018` on a trusted network. Outbound traffic (backend, `wss://` relay, S3, OTLP) is always TLS.
- For TLS, front the API with a reverse proxy and set `BASE_URL=https://<host>` (enables HSTS, silences the plaintext-LAN startup warning).

Network notes:

- Keep app port `8018` reachable only on trusted operator networks.
- MediaMTX RTSP `8554` and HLS `8888` bind to `127.0.0.1`; preview traffic goes through the app.
- Ethernet link-local addressing (`169.254.x.x`) works when no DHCP server is present.
- USB gadget mode applies to Raspberry Pi Zero 2W and some Raspberry Pi 4 revisions, not Raspberry Pi 5.
- mDNS is optional. Install `avahi-daemon` and advertise `_relab-rpi-cam._tcp` on port 8018 to reach the Pi at `<hostname>.local`.

## Direct Python Run

For a non-Docker run on the Pi:

```sh
./scripts/local_setup.sh
uv run fastapi run app/main.py --host 0.0.0.0 --port 8018 --forwarded-allow-ips 127.0.0.1,::1
```

## Verify The Service

Once running, check:

- setup and status: `http://your-pi-ip:8018/setup`
- API reference: <https://docs.cml-relab.org/api/rpi-cam/>
- HLS preview: `http://your-pi-ip:8018/preview/hls/cam-preview/index.m3u8`

For headless operation, read pairing status from:

- Docker Compose: `docker compose logs app`
- systemd/journald: `journalctl -u relab-rpi-cam -f`
- direct shell run: the `PAIRING READY` terminal banner

## Observability

Structured JSON logs are always written. Docker logs are bounded by Compose config, and rotating file logs are written to the mounted `app_logs` volume.

Log inventory:

- app file logs: UTC JSON in `app_logs`, rotated daily
- Docker logs: redacted console output
- optional shipped logs: Alloy tails `app_logs` and pushes to the configured collector
- security events: auth results, rate-limit denials, relay validation failures, and unhandled errors

Logs include request IDs where available and leave out credentials, tokens, pairing codes, private keys, request bodies, and response bodies.

To ship logs to a Loki-compatible collector, add:

```sh
COMPOSE_PROFILES=observability-ship
OBSERVABILITY_INSTANCE=pi-01
LOKI_PUSH_URL=https://your-observability-host/loki/api/v1/push
```

Use an authenticated collector with explicit log retention. Structured file logs
redact runtime secrets and omit local-only pairing banners, but collectors
remain operator-visible infrastructure.

To ship metrics, configure a remote-write endpoint and an API key for Alloy's
local scrape:

```sh
PROMETHEUS_REMOTE_WRITE_URL=https://your-observability-host/api/v1/write
PROMETHEUS_SCRAPE_API_KEY=change-me
AUTHORIZED_API_KEYS='["change-me"]'
```

The scrape key is sent only to the local plugin `/metrics` endpoint. Use scoped
collector credentials for remote-write when supported.

Tracing is opt-in:

```sh
OTEL_ENABLED=true
OTEL_EXPORTER_OTLP_ENDPOINT=https://your-observability-host:4318/v1/traces
```

Remote production OTLP endpoints require HTTPS. Plaintext HTTP is accepted only
for loopback collectors or when `APP_ENV=development`.
If you rotate `PROMETHEUS_SCRAPE_API_KEY`, `AUTHORIZED_API_KEYS`,
Loki/Prometheus tokens, or OTLP collector credentials, update the collector
first, then refresh `.env` and restart the stack.

Local Loki/Grafana and OTLP collectors are not bundled with this plugin.

Profiles can be combined:

```sh
COMPOSE_PROFILES=standalone,observability-ship
```

## Troubleshooting

### Camera Not Detected

```sh
rpicam-hello --list-cameras
```

Check the CSI cable orientation, camera seating, and camera module compatibility.

### API Does Not Start

- Check port 8018 availability: `sudo netstat -tlnp | grep :8018`
- Check logs: `docker compose logs app`
- For direct runs, start with `uv run fastapi run app/main.py --host 0.0.0.0 --port 8018 --forwarded-allow-ips 127.0.0.1,::1`

### Relay Does Not Connect

- Confirm `PAIRING_BACKEND_URL` points at the backend API origin.
- Confirm the Pi has outbound internet access.
- Confirm `~/.config/relab/relay_credentials.json` exists after pairing.
- Check logs: `docker compose logs app`
- If the backend sits behind Cloudflare, add a WAF bypass for `/v1/plugins/rpi-cam/pairing/*`, `/v1/plugins/rpi-cam/device/*`, and `/v1/plugins/rpi-cam/ws/connect`.

### Pairing Code Not Showing

- Confirm `PAIRING_BACKEND_URL` is set in `.env`.
- Remove `~/.config/relab/relay_credentials.json` if pairing should restart.
- Check `/setup` and the `PAIRING MODE` / `PAIRING READY` log lines.
- In Docker, avoid `http://localhost:8011` for host services from inside the container. Use `http://host.docker.internal:8011`, the host LAN IP, or the real HTTPS API URL.

### Poor Image Quality

- Clean the camera lens gently.
- Improve lighting at the capture location.
- Check the camera module connection.
