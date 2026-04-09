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
   Go to Cameras > Add Camera, select WebSocket mode, and enter the pairing code (or scan the QR code).

1. **Done**\
   The Pi automatically receives credentials, saves them to `~/.config/relab/relay_credentials.json`, and connects to the backend.

### Option B: Manual WebSocket Setup

For cases where automatic pairing isn't available.

1. **Register in RELab app**\
   Cameras > Add Camera > WebSocket mode > Manual setup.

1. **Copy credentials to file**\
   Create `~/.config/relab/relay_credentials.json`:

   ```json
   {
     "relay_backend_url": "wss://api.cml-relab.org/plugins/rpi-cam/ws/connect",
     "relay_camera_id": "<your-camera-id>",
     "relay_api_key": "<your-api-key>"
   }
   ```

   Or set `RELAB_CREDENTIALS_FILE` to use a custom path.

1. **Restart the plugin**

## Step 3: Running the Application

### Docker (recommended)

1. **Generate device mapping**

   ```sh
   ./scripts/generate_compose_override.py > compose.override.yml
   ```

1. **Start the stack**

   ```sh
   docker compose build
   docker compose up -d
   ```

   To include the optional observability stack for local log browsing on one machine, which runs Alloy, Loki, and Grafana together:

   ```sh
   docker compose --profile observability-ship --profile observability-collect up -d
   ```

   To ship logs from a Pi to a separate central collector:

   ```sh
   export OBSERVABILITY_INSTANCE=pi-01
   export LOKI_PUSH_URL=http://your-central-host:3100/loki/api/v1/push
   docker compose --profile observability-ship up -d
   ```

   On the central collector host, run:

   ```sh
   docker compose --profile observability-collect up -d
   ```

   If you do not enable either observability profile, the app still writes structured log files to the mounted `app_logs` volume. You just won’t have the Alloy/Loki/Grafana UI layer or log shipping enabled.

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
   PAIRING READY | code=ABC123 setup=/setup pairing_backend=https://api.cml-relab.org claim_in='RELab app > Cameras > Add Camera'
   ```

## Testing

Once running, verify at:

- **Setup & Status:** `http://your-pi-ip:8018/setup`
- **API Docs:** `http://your-pi-ip:8018/docs`

For headless operators, you can also read the pairing code from logs:

- Docker Compose: `docker compose logs app`
- Systemd/journald: `journalctl -u relab-rpi-cam -f`
- Direct shell run: read the `PAIRING READY` line in the terminal output

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

- Verify `~/.config/relab/relay_credentials.json` exists with valid credentials
- Check Pi has outbound internet access
- Confirm API key matches platform registration (regenerate if unsure)
- Check plugin logs: `docker compose logs app`
- If `observability-collect` is enabled, open Grafana at `http://your-host-ip:3000` and inspect Loki logs there.

### Pairing code not showing

- Ensure `PAIRING_BACKEND_URL` is set in `.env`
- Remove `~/.config/relab/relay_credentials.json` if it exists (pairing is skipped when credentials present)
- Check `/setup` or look for the `PAIRING MODE` and `PAIRING READY` log lines

### Poor image quality

- Clean camera lens gently
- Improve lighting at capture location
- Check camera module connection
