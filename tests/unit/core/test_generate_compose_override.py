"""Tests for the `generate_compose_override` script."""

from scripts.generate_compose_override import write_compose_override
from tests.constants import COMPOSE_OVERRIDE_APP_ONE_DEVICE, COMPOSE_OVERRIDE_CUSTOM_NO_DEVICES


def test_write_compose_override_defaults_to_app_service() -> None:
    """Should generate override for 'app' service by default."""
    text = write_compose_override(["/dev/video0"])

    assert text == COMPOSE_OVERRIDE_APP_ONE_DEVICE


def test_write_compose_override_can_target_custom_service() -> None:
    """Should generate override for specified service name."""
    text = write_compose_override([], service_name="rpi-cam-plugin")

    assert text == COMPOSE_OVERRIDE_CUSTOM_NO_DEVICES
