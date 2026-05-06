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

View logs:

```sh
docker compose logs -f app
```

### Pair The Camera

When pairing mode is active, read the code from either surface:

- browser UI: `http://your-pi-ip:8018/setup`
- logs: the `PAIRING READY` banner

Treat `/setup` and pairing logs as local/operator-only during pairing. The code is a short-lived bootstrap credential; anyone who can reach either surface during its 10-minute window can try to claim the camera.

Enter the code in the native RELab app under Cameras > Add Camera. The Pi receives relay credentials, saves them to `~/.config/relab/relay_credentials.json`, and connects to the backend.

To rotate the code without deleting relay credentials, use **Generate a new pairing code** on `/setup`.

Docker Compose stores runtime credentials in a named volume mounted at `/home/rpicam/.config/relab`, so paired credentials survive container restarts.

The HTTPS-served RELab web frontend cannot auto-probe the Pi's plain-HTTP local API because browsers block mixed content. Use the native app for pairing and direct-mode setup.

## Standalone Mode

Standalone mode stores captures in an S3-compatible bucket instead of the RELab backend. The bundled standalone profile starts a local RustFS sidecar.

Set these values in `.env`:

```sh
APP_BUILD_TARGET=runtime-standalone
COMPOSE_PROFILES=standalone
APP_ENV=development

IMAGE_SINK=s3
S3_ENDPOINT_URL=http://host.docker.internal:9000
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
- RustFS console: `http://<pi-lan-ip>:9001`
- Captures: `http://<pi-lan-ip>:9000/rpi-cam/`

For an external S3-compatible service such as Backblaze B2, Cloudflare R2, Wasabi, or AWS S3, set `S3_ENDPOINT_URL`, credentials, and `S3_PUBLIC_URL_TEMPLATE`. Remote production S3 endpoints require HTTPS. Keep `APP_ENV=development` for local HTTP storage only.

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

Network notes:

- Ethernet link-local addressing (`169.254.x.x`) works when no DHCP server is present.
- USB gadget mode applies to Raspberry Pi Zero 2W and some Raspberry Pi 4 revisions, not Raspberry Pi 5.
- mDNS is optional. Install `avahi-daemon` and advertise `_relab-rpi-cam._tcp` on port 8018 to reach the Pi at `<hostname>.local`.

## Direct Python Run

For a non-Docker run on the Pi:

```sh
./scripts/local_setup.sh
uv run fastapi run app/main.py --host 0.0.0.0 --port 8018
```

## Verify The Service

Once running, check:

- setup and status: `http://your-pi-ip:8018/setup`
- API docs: `http://your-pi-ip:8018/docs`
- HLS preview: `http://your-pi-ip:8018/preview/hls/cam-preview/index.m3u8`

For headless operation, read pairing status from:

- Docker Compose: `docker compose logs app`
- systemd/journald: `journalctl -u relab-rpi-cam -f`
- direct shell run: the `PAIRING READY` terminal banner

## Observability

Structured JSON logs are always written. Docker logs are bounded by Compose config, and rotating file logs are written to the mounted `app_logs` volume.

To ship logs to a Loki-compatible collector, add:

```sh
COMPOSE_PROFILES=observability-ship
OBSERVABILITY_INSTANCE=pi-01
LOKI_PUSH_URL=http://your-observability-host:3100/loki/api/v1/push
```

Tracing is opt-in:

```sh
OTEL_ENABLED=true
OTEL_EXPORTER_OTLP_ENDPOINT=https://your-observability-host:4318/v1/traces
```

Remote production OTLP endpoints require HTTPS. Plaintext HTTP is accepted only for loopback collectors or when `APP_ENV=development`.

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
- For direct runs, start with `uv run fastapi run app/main.py --host 0.0.0.0 --port 8018`

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
