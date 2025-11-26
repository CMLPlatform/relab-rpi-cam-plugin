#!/usr/bin/env python3
"""Map camera device nodes to docker-compose override file.

This script discovers camera-related devices nodes and generates a docker compose
with a `devices:` mapping for the `rpi-cam-plugin` service.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docker-compose.override.yml"
DEVICE_PATTERNS = ("/dev/media*", "/dev/video*", "/dev/v4l-subdev*", "/dev/dma_heap")


def discover_devices(patterns: tuple[str, ...] = DEVICE_PATTERNS) -> list[str]:
    """Discover camera-related device nodes."""
    found = set()
    for p in patterns:
        for f in Path("/").glob(p.lstrip("/")):
            if f.exists():
                found.add(str(f))
    return sorted(found)


def write_compose_override(device_paths: list[str], service_name: str = "rpi-cam-plugin") -> str:
    """Generate a minimal docker-compose override with devices for the service.

    Output format (example):

    services:
      rpi-cam-plugin:
        devices:
          - "/dev/video0:/dev/video0"

    The function prints the YAML to stdout and returns it as a string.
    """
    lines: list[str] = ["services:", f"  {service_name}:"]
    if device_paths:
        lines.append("    devices:")
        lines.extend(f'      - "{p}:{p}"' for p in device_paths)
    else:
        lines.append("    devices: []")

    final_text = "\n".join(lines) + "\n"
    print(final_text)  # noqa: T201 # printing to stdout is intended
    return final_text


def main() -> int:
    """Main entry point."""
    device_paths = discover_devices()
    write_compose_override(device_paths)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
