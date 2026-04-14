# Installation & Setup Guide

Complete setup instructions for the RPI Camera Plugin.

## Requirements

### Hardware

- Raspberry Pi 5 (recommended) or Pi 4
- Raspberry Pi Camera Module 3 (recommended) or v2
- MicroSD card (8GB or larger)
- Power supply (wall adapter or power bank)
- Network connection (Ethernet or WiFi)
- Camera mount (tripod, clamp, or custom)

### Software

- Raspberry Pi OS (64-bit recommended)
- Python 3.13+
- Network connectivity to RELab platform

## Step 1: Prepare Your Raspberry Pi

1. **Install Raspberry Pi OS**\
   Follow the [official installation guide](https://www.raspberrypi.com/documentation/computers/getting-started.html#installing-the-operating-system).

1. **Connect camera module**\
   Attach the camera to the Pi's camera port. See the [camera module guide](https://www.raspberrypi.com/documentation/accessories/camera.html#connect-the-camera) for alignment.

1. **Test camera**

   ```sh
   rpicam-hello
   ```

1. **Clone repository**

   ```sh
   git clone https://github.com/CMLPlatform/relab-rpi-cam-plugin.git
   cd relab-rpi-cam-plugin
   ```

## Step 2: Configure Connection

The plugin connects to the RELab backend via **WebSocket relay**. The Pi initiates an outbound connection — no public IP or port forwarding is needed.

### Option A: Automatic Pairing (recommended)

The simplest approach. No credential exchange required.

1. **Create `.env` file**

   ```sh
   cp .env.example .env
   ```

1. **Set the pairing backend URL**

   ```sh
   PAIRING_BACKEND_URL=https://api.cml-relab.org
   ```

1. **Start the plugin** (see [Step 3](#step-3-running-the-application)).

1. **Read the pairing code**
   Use either of these supported setup paths:

   - Browser UI: visit `http://your-pi-ip:8018/setup`
   - Headless over SSH/logs: watch for the `PAIRING READY` log line

1. **Pair in RELab app**\
   Go to Cameras > Add Camera and enter the pairing code.

1. **Done**\
   The Pi automatically receives credentials, saves them to `~/.config/relab/relay_credentials.json`, and connects to the backend.

When you run the plugin via Docker Compose, `compose.yml` persists this directory with a named
volume at `/home/rpicam/.config/relab`, so paired relay credentials survive container restarts.

## Step 3: Running the Application

### Docker (recommended)

1. **Generate device mapping**

   ```sh
   ./scripts/generate_compose_override.py > compose.override.yml
   ```

   The generated override targets the existing `app` service from `compose.yml`, so Compose can merge the device mappings into the plugin container.

1. **Start the stack**

   ```sh
   docker compose build
   docker compose up -d
   ```

   View logs with:

   ```sh
   docker compose logs -f app
   ```

   To ship logs from a Pi to an external Loki-compatible collector:

   ```sh
   export OBSERVABILITY_INSTANCE=pi-01
   export LOKI_PUSH_URL=http://your-observability-host:3100/loki/api/v1/push
   docker compose --profile observability-ship up -d
   ```

   If you do not enable `observability-ship`, the app still writes bounded Docker logs and structured 7-day rotating logs to the mounted `app_logs` volume. Local Loki/Grafana is not bundled with this plugin; use a central observability stack when you need fleet log browsing.

### Direct on Pi

1. **Prepare environment**

   ```sh
   ./scripts/local_setup.sh
   ```

1. **Start server**

   ```sh
   uv run fastapi run app/main.py --port 8018
   ```

   When pairing mode is active, the terminal prints a line like:

   ```text
   +----------------------------------------------+
   | PAIRING READY                                |
   | code: ABC123                                 |
   | setup: /setup                                |
   | backend: https://api.cml-relab.org          |
   | claim in: RELab app > Cameras > Add Camera  |
   +----------------------------------------------+
   ```

## Testing

Once running, verify at:

- **Setup & Status:** `http://your-pi-ip:8018/setup`
- **API Docs:** `http://your-pi-ip:8018/docs`

For headless operators, you can also read the pairing code from logs:

- Docker Compose: `docker compose logs app`
- Systemd/journald: `journalctl -u relab-rpi-cam -f`
- Direct shell run: read the boxed `PAIRING READY` banner in the terminal output

## Troubleshooting

### Camera not detected

```sh
rpicam-hello --list-cameras
```

Verify camera is properly connected to the CSI port.

### API won't start

- Check port 8018 availability: `sudo netstat -tlnp | grep :8018`
- Try dev mode: `uv run fastapi dev app/main.py`
- Check logs for Python errors

### WebSocket relay won't connect

- Verify `~/.config/relab/relay_credentials.json` exists (created automatically after pairing)
- Check Pi has outbound internet access
- Check plugin logs: `docker compose logs app`
- If `observability-ship` is enabled, inspect the external Loki/Grafana collector configured by `LOKI_PUSH_URL`.

### Pairing code not showing

- Ensure `PAIRING_BACKEND_URL` is set in `.env`

- If the plugin runs in Docker, `http://localhost:8011` points at the plugin container itself, not your host machine. Use `http://host.docker.internal:8011`, your host's LAN IP, or the real HTTPS API URL instead.

- If `PAIRING_BACKEND_URL=https://api-test.cml-relab.org` returns `403` during `/plugins/rpi-cam/pairing/register` or `/plugins/rpi-cam/ws/connect`, the request is likely being blocked by Cloudflare before it reaches FastAPI. Add a WAF/challenge bypass rule for both `api-test.cml-relab.org` and `api.cml-relab.org` covering the machine-facing RPi camera paths:

  ```text
  (
    http.host in {"api-test.cml-relab.org" "api.cml-relab.org"}
    and starts_with(http.request.uri.path, "/plugins/rpi-cam/pairing/")
  )
  or
  (
    http.host in {"api-test.cml-relab.org" "api.cml-relab.org"}
    and http.request.uri.path eq "/plugins/rpi-cam/ws/connect"
  )
  ```

  Set the action to skip or bypass the Cloudflare security feature issuing the challenge. In April 2026, `api-test.cml-relab.org` returned `cf-mitigated: challenge` on both the pairing register path and the WebSocket relay path.

- Remove `~/.config/relab/relay_credentials.json` if it exists (pairing is skipped when credentials present)

- Check `/setup` or look for the `PAIRING MODE` and `PAIRING READY` log lines

### Poor image quality

- Clean camera lens gently
- Improve lighting at capture location
- Check camera module connection
