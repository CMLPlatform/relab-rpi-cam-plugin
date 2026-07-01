#!/usr/bin/env python3
"""Generate compose override with camera device mappings for rpi-cam-plugin.

Discovers camera-related device nodes (/dev/media*, /dev/video*, /dev/v4l-subdev*,
/dev/dma_heap) and generates compose.override.yml mapping them into the container.
This enables Picamera2 hardware access without requiring privileged mode.
"""

import sys
from pathlib import Path

DEVICE_PATTERNS = ("/dev/media*", "/dev/video*", "/dev/v4l-subdev*", "/dev/dma_heap/*")


def discover_devices(patterns: tuple[str, ...] = DEVICE_PATTERNS) -> list[str]:
    """Discover camera-related device nodes."""
    found = set()
    for p in patterns:
        found.update(str(f) for f in Path("/").glob(p.lstrip("/")))
    return sorted(found)


def write_compose_override(device_paths: list[str], service_name: str = "app") -> str:
    """Build a minimal compose override with devices for the service."""
    lines: list[str] = ["services:", f"  {service_name}:"]
    if device_paths:
        lines.append("    devices:")
        lines.extend(f'      - "{p}:{p}"' for p in device_paths)
    else:
        lines.append("    devices: []")
    return "\n".join(lines) + "\n"


def main() -> int:
    """Main entry point."""
    device_paths = discover_devices()
    sys.stdout.write(write_compose_override(device_paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
