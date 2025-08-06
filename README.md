# RPI Camera Plugin

Device-side software for automated image capture on Raspberry Pi, integrated with the Reverse Engineering Lab platform.

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

1. **Enable camera interface**:

   ```bash
   sudo raspi-config
   # Navigate to: Interface Options → Camera → Enable
   sudo reboot
   ```

1. **Test camera**:

   ```bash
   libcamera-hello --preview
   ```

### Step 2: Install Camera Software

1. **Clone repository**:

   ```bash
   git clone https://github.com/CMLPlatform/relab-rpi-cam-plugin.git
   cd relab-rpi-cam-plugin
   ```

1. **Run setup script**:

   ```bash
   ./setup.sh
   ```

   This automatically:

   - Verifies environment configuration
   - Sets up audio streaming capabilities
   - Installs dependencies using `uv` (a [Python package manager](https://docs.astral.sh/uv/))
   - Creates Python virtual environment

### Step 3: Get Platform Credentials

<!-- TODO: Replace by description of UI flow on main platform once available -->

Before configuring your device, register it on the platform at the `plugins/rpi-cam/cameras` endpoint by providing:

- Name
- Camera API URL (e.g., `http://your-pi-ip:8018`)
- Description (optional)
- Additional auth headers required to access the Camera API URL (optional)

💡 Save the returned API key - it will only be shown once.

### Step 4: Configure Your Camera

1. **Create configuration**:

   ```bash
   cp .env.example .env
   ```

1. **Edit settings** in `.env`:

   ```bash
   # Your Pi's network details
   BASE_URL="http://your-pi-ip:8018"

   # Platform integration
   ALLOWED_CORS_ORIGINS=["http://127.0.0.1:8000", "https://your-platform.com"]
   AUTHORIZED_API_KEYS=["your-api-key-from-platform"]
   ```

**Security Note**: Keep API keys secure and never commit them to version control.

### Step 5: Start Camera Service

1. **Launch camera API**:

   ```bash
   uv run fastapi run app/main.py --port 8018
   ```

1. **Test installation**:

   - API documentation: `http://your-pi-ip:8018/docs`
   - Live stream: `http://your-pi-ip:8018/stream/watch`
   - Capture test: Use `/capture` endpoint

1. **Verify platform connection**:

   <!-- TODO: Replace by description of UI flow on main platform once available -->

   - Access camera from main platform at the `/plugins/rpi-cam/camera/{camera_id}` endpoint

## Usage

### Local Testing

- **API documentation**: Full interactive API available at `/docs` endpoint
- **Live preview**: Real-time camera feed at `/stream/watch`
- **Manual capture**: Test image capture using `/capture` endpoint
- **Health monitoring**: Check device status at `/status`

### Production Operation

Once configured, the camera can be operated from the main platform. See
[Platform Documentation](https://docs.cml-relab.org/user-guides/rpi-cam/) for more details.

## Troubleshooting

**Camera not detected**:

```bash
# Check camera connection
vcgencmd get_camera

# Test camera module
libcamera-hello --list-cameras
```

**API won't start**:

- Check that port 8018 is available: `sudo netstat -tlnp | grep :8018`
- Verify all dependencies installed correctly
- Review error logs for specific issues
- Test with debug mode: `uv run python app/main.py --log-level debug`

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
- Adjust camera settings through API parameters

## Development

### Development Environment

```bash
# Install with development dependencies
uv sync

# Install pre-commit hooks
uv run pre-commit install

# Start development server with hot reload
uv run fastapi dev
```
