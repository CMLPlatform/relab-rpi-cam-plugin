"""Validate a models package tag against the package version."""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

DEFAULT_PREFIX = "relab-rpi-cam-models-v"
DEFAULT_PYPROJECT = Path("relab_rpi_cam_models/pyproject.toml")


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag_name", help="The tag name that triggered publishing.")
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=DEFAULT_PYPROJECT,
        help=f"Path to the models package pyproject file (default: {DEFAULT_PYPROJECT})",
    )
    parser.add_argument(
        "--prefix",
        default=DEFAULT_PREFIX,
        help=f"Expected tag prefix (default: {DEFAULT_PREFIX})",
    )
    return parser.parse_args()


def main() -> int:
    """Validate that the tag suffix matches the package version."""
    args = parse_args()
    tag_name = args.tag_name
    if not tag_name.startswith(args.prefix):
        sys.stderr.write(f"Unexpected tag name: {tag_name}\n")
        return 1

    package_version = tomllib.loads(args.pyproject.read_text(encoding="utf-8"))["project"]["version"]
    tag_version = tag_name.removeprefix(args.prefix)
    if tag_version != package_version:
        sys.stderr.write(
            f"Tag version {tag_version} does not match relab_rpi_cam_models version {package_version}\n",
        )
        return 1

    sys.stdout.write(f"Validated {tag_name} against relab_rpi_cam_models {package_version}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
