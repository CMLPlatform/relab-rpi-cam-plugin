# RPI Camera Plugin

Device-side software for automated image capture on Raspberry Pi, integrated with the [Reverse Engineering Lab platform](https://cml-relab.org).

## Overview

This guide covers **installing and configuring the plugin on Raspberry Pi devices**. For platform-side camera management, see the [Platform Documentation](https://docs.cml-relab.org/user-guides/rpi-cam/).

## Hardware Requirements

- Raspberry Pi 5 (recommended) or Pi 4
- Raspberry Pi Camera Module 3 (recommended) or v2
- MicroSD card (8GB or larger)
- Power supply (wall adapter or power bank)
- Network connection (Ethernet or WiFi)
- Camera mount (tripod, clamp, or custom)

## Software Requirements

- Raspberry Pi OS (64-bit recommended)
- Python 3.11+
- Network connectivity to RELab platform

## Quick Setup

### Step 1: Prepare Your Raspberry Pi

1. **Install Raspberry Pi OS**: Follow the [official installation guide](https://www.raspberrypi.com/documentation/computers/getting-started.html#installing-the-operating-system)

1. **Connect camera module**: Attach the camera to the Pi's camera port, ensuring proper alignment and secure connection. Refer to the [camera module guide](https://www.raspberrypi.com/documentation/accessories/camera.html#connect-the-camera) for details.

1. **Test camera**:

   ```sh
   rpicam-hello
   ```

1. **Clone repository**:

   ```sh
   git clone https://github.com/CMLPlatform/relab-rpi-cam-plugin.git
   cd relab-rpi-cam-plugin
   ```

### Step 3: Get Platform Credentials

<!-- TODO: Replace by description of UI flow on main platform once available -->

Before configuring your device, register it on the platform at the `plugins/rpi-cam/cameras` endpoint by providing:

- Name
- Camera API URL (e.g., `http://your-pi-ip:8018`) (If using Cloudflare Tunnel, use the tunnel URL here. See the cloudflare setup instructions in [step 6](#step-6-publishing-the-api-to-the-internet-optional))
- Description (optional)
- Additional auth headers required to access the Camera API URL (optional)

💡 Save the returned API key - it will only be shown once.

### Step 4: Configure Your Camera

1. **Create configuration**:

   ```sh
   cp .env.example .env
   ```

1. **Edit settings** in `.env`:

   - `BASE_URL`: Set to your the URL at which your API can be accessed (e.g., `http://your-pi-ip:8018`)
   - `AUTHORIZED_API_KEYS`: Add the API key obtained from the platform registration

1. Optionally, you can also adjust:

   - `ALLOWED_CORS_ORIGINS`: Ensure it includes the platform URL (e.g., `https://cml-relab.org` and `https://api.cml-relab.org`)
   - `CAMERA_DEVICE_NUM`: Set to the camera device number (usually `0`)
   - `TUNNEL_TOKEN`: Add your Cloudflare Tunnel token if using Cloudflare Tunnel (see [step 6](#step-6-publishing-the-api-to-the-internet-optional))

### Step 5: Running the application

You can either run the application inside Docker (recommended) or directly on the Pi.

#### Docker (recommended)

- Generate a compose override that maps camera devices into the `rpi-cam-plugin` service. On the Pi host run:

```sh
./scripts/generate_docker_compose_override.py > docker-compose.override.yml
```

- Build and start the stack with the generated override (from the repo root):

```sh
docker compose build
docker compose up -d
```

#### Run directly on the Pi

- Prepare the Pi for running the app:

```sh
./scripts/local_setup.sh
```

- Start the FastAPI server directly:

```sh
uv run fastapi run app/main.py --port 8018
```

### Step 6: Publishing the API to the internet (optional)

You can use your preferred reverse proxy or expose via Cloudflare Tunnel. The latter is supported directly in the Docker Compose setup.

1. Follow the [Cloudflare Tunnel documentation](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/) to create a remotely managed tunnel and obtain a tunnel token.

1. Set the tunnel token in your `.env`:

   ```sh
   TUNNEL_TOKEN=your_tunnel_token_here
   ```

1. Start the compose stack with the `cloudflared` profile:

   ```sh
   docker compose --profile cloudflared up -d
   ```

1. **Publish your app** Follow the [documentation](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/#2a-publish-an-application) on publishing an app via the tunnel. Set the hostname (e.g., `yourcamera.yourdomain.com`) and point to the service (`http://rpi-cam-plugin:8018`).

> 💡 **Note**: If using Docker, the service name is `rpi-cam-plugin` as defined in the `docker-compose.yml`. If running directly on the Pi, use `http://localhost:8018`.

## Usage

### Local Testing

- **API documentation**: Full interactive API available at `/docs` endpoint
- **Live preview**: Real-time camera feed at `/stream/watch`
- **Manual capture**: Test image capture using `/capture` endpoint
- **Health monitoring**: Check device status at `/status`

### Production Operation

Once configured, the camera can be operated from the main platform. See
[Platform Documentation](https://docs.cml-relab.org/user-guides/rpi-cam/) for more details.

### Troubleshooting

**Camera not detected**:

```sh
# Check camera connection
rpicam-hello --list-cameras
```

**API won't start**:

- Check that port 8018 is available: `sudo netstat -tlnp | grep :8018`
- Test with dev mode: `uv run fastapi dev app/main.py`

**Platform can't connect**:

- Confirm API key matches platform registration
- Check CORS origins include platform URL
- Test network connectivity between Pi and platform
- Verify firewall rules on both sides
- Test API directly at `http://your-pi-ip:8018/docs`

**Poor image quality**:

- Clean camera lens carefully
- Improve lighting conditions at capture location
- Check camera module connection to Pi

## Development

### Local development setup

For local development on the Raspberry Pi, run the local setup script which creates a dev environment:

```sh
./scripts/local_setup.sh --dev
```

This script runs `uv sync` for a development environment and configures tooling (pre-commit, venv, etc.). Use `uv run fastapi dev app/main.py` to start the app with hot reload.

You can run the development server with:

```sh
uv run fastapi dev app/main.py --port 8018
```
