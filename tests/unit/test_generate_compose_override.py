from scripts.generate_compose_override import write_compose_override


def test_write_compose_override_defaults_to_app_service() -> None:
    text = write_compose_override(["/dev/video0"])

    assert text == 'services:\n  app:\n    devices:\n      - "/dev/video0:/dev/video0"\n'


def test_write_compose_override_can_target_custom_service() -> None:
    text = write_compose_override([], service_name="rpi-cam-plugin")

    assert text == "services:\n  rpi-cam-plugin:\n    devices: []\n"
