"""Fail when the workspace r9cam-models version is ahead of PyPI with no release tag.

The R9lab backend installs the contract package from PyPI while the plugin builds it from
this workspace. A version bump that is never tagged leaves the two sides of the device seam
validating against different code.
"""

import json
import subprocess
import sys
import tomllib
import urllib.request
from pathlib import Path

PYPROJECT = Path("r9cam_models/pyproject.toml")
PYPI_URL = "https://pypi.org/pypi/r9cam-models/json"
TAG_PREFIX = "r9cam-models-v"


def find_drift(version: str, published: set[str], *, tag_exists: bool) -> str | None:
    """Return a message when the version is neither on PyPI nor tagged for publishing."""
    if version in published or tag_exists:
        return None
    return (
        f"r9cam_models is at {version}, which is not on PyPI (published: {', '.join(sorted(published))}) "
        f"and has no {TAG_PREFIX}{version} tag. Push the tag to publish it."
    )


def main() -> int:
    """Compare the workspace version with PyPI and the local tags."""
    version = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
    with urllib.request.urlopen(PYPI_URL, timeout=30) as response:
        published = set(json.load(response)["releases"])
    tag = f"refs/tags/{TAG_PREFIX}{version}"
    # Fixed git argv with no shell, so there is no untrusted input to inject.
    git = ["git", "rev-parse", "-q", "--verify", tag]
    tag_exists = subprocess.run(git, capture_output=True, check=False).returncode == 0  # noqa: S603

    problem = find_drift(version, published, tag_exists=tag_exists)
    if problem:
        sys.stderr.write(problem + "\n")
        return 1
    sys.stdout.write(f"r9cam_models {version} is published or tagged\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
