#!/usr/bin/env python3
"""Map camera device nodes to docker-compose override file.

This script discovers camera-related devices nodes and writes `docker-compose.override.yml`
with a `devices:` mapping for the `rpi-cam-plugin` service.
"""

import glob
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docker-compose.override.yml"
PATTERNS = ("/dev/media*", "/dev/video*", "/dev/v4l-subdev*", "/dev/dma_heap")


def discover_devices(patterns: tuple[str] = PATTERNS) -> list[str]:
    """Discover camera-related device nodes."""
    found = set()
    for p in patterns:
        for f in glob.glob(p):
            if Path(f).exists():
                found.add(f)
    return sorted(found)


def write_compose_override(device_paths: list[str], service_name: str = "rpi-cam-plugin") -> None:
    """Write docker-compose.override.yml with device mappings."""
    lines = [
        "services:",
        f"  {service_name}:",
        "    devices:",
    ]

    if not device_paths:
        print("No camera-related devices found, not writing override.")
        return

    lines.extend(f"      - '{p}:{p}'" for p in device_paths)

    lines += [
        "    volumes:",
        "      - /run/udev:/run/udev:ro",
    ]

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}")
    print("Mapped devices:")
    for p in device_paths:
        print("  ", p)


def main() -> int:
    device_paths = discover_devices()
    write_compose_override(device_paths)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
