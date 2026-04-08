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

Choose **one** connection method below.

### Option A: Automatic Pairing (WebSocket, recommended)

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

1. **Open setup page**\
   Visit `http://your-pi-ip:8018/setup` to see your 6-character pairing code.

1. **Pair in RELab app**\
   Go to Cameras > Add Camera, select WebSocket mode, and enter the pairing code (or scan the QR code).

1. **Done**\
   The Pi automatically receives credentials, saves them to `relay_credentials.json`, and connects to the backend.

### Option B: Manual WebSocket Setup

For cases where automatic pairing isn't available.

1. **Register in RELab app**\
   Cameras > Add Camera > WebSocket mode > Manual setup.

1. **Copy credentials to file**\
   Create `relay_credentials.json` in the plugin directory:

   ```json
   {
     "relay_backend_url": "wss://api.cml-relab.org/plugins/rpi-cam/ws/connect",
     "relay_camera_id": "<your-camera-id>",
     "relay_api_key": "<your-api-key>"
   }
   ```

1. **Restart the plugin**

### Option C: Direct HTTP

For Pi with public IP or VPN access.

1. **Register in RELab app**\
   Cameras > Add Camera > HTTP mode. Provide your camera's URL (e.g., `http://your-pi-ip:8018`).

1. **Create `.env` file**

   ```sh
   cp .env.example .env
   ```

1. **Configure connection settings**

   - `BASE_URL` — Where your API is accessible (e.g., `http://your-pi-ip:8018`)
   - `AUTHORIZED_API_KEYS` — The API key from the platform

1. **Optional settings**

   - `ALLOWED_CORS_ORIGINS` — Include platform URLs (`https://cml-relab.org`, `https://api.cml-relab.org`)
   - `CAMERA_DEVICE_NUM` — Camera device (usually `0`)

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

### Direct on Pi

1. **Prepare environment**

   ```sh
   ./scripts/local_setup.sh
   ```

1. **Start server**

   ```sh
   uv run fastapi run app/main.py --port 8018
   ```

## Step 4: Expose to Internet (HTTP mode only)

Skip this if using WebSocket relay.

### Option: Cloudflare Tunnel

1. **Get tunnel token**\
   Follow [Cloudflare's tunnel setup guide](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/).

1. **Set token in `.env`**

   ```sh
   TUNNEL_TOKEN=your_tunnel_token_here
   ```

1. **Start tunnel**

   ```sh
   docker compose --profile cloudflared up -d
   ```

1. **Publish your app**\
   Follow [Cloudflare's publishing guide](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/#2a-publish-an-application). Point to `http://rpi-cam-plugin:8018` (or `http://localhost:8018` if running directly).

## Testing

Once running, verify at:

- **Setup & Status:** `http://your-pi-ip:8018/setup`
- **API Docs:** `http://your-pi-ip:8018/docs`
- **Live Stream:** `http://your-pi-ip:8018/stream/watch`
- **Capture Test:** `http://your-pi-ip:8018/capture`
- **Health Check:** `http://your-pi-ip:8018/status`

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

- Verify `relay_credentials.json` exists with valid credentials
- Check Pi has outbound internet access
- Confirm API key matches platform registration (regenerate if unsure)
- Check plugin logs: `docker compose logs rpi-cam-plugin`

### Pairing code not showing

- Ensure `PAIRING_BACKEND_URL` is set in `.env`
- Remove `relay_credentials.json` if it exists (pairing is skipped when credentials present)
- Check logs for pairing registration errors

### Platform can't connect (HTTP mode)

- Verify API key matches platform registration
- Check `ALLOWED_CORS_ORIGINS` includes platform URLs
- Test network connectivity between Pi and platform
- Verify firewall rules allow inbound traffic
- Test API directly: `http://your-pi-ip:8018/docs`

### Poor image quality

- Clean camera lens gently
- Improve lighting at capture location
- Check camera module connection
