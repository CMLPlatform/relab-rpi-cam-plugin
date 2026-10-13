# Changelog

All notable changes to relab-rpi-cam-models are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the package uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Releases before 0.5.2 are listed
only in the GitHub tags (`relab-rpi-cam-models-v*`).

## [Unreleased]

## [0.5.2] - 2026-10-13

0.5.1 was never published to PyPI, so this release carries its changes too. Upgrade the
backend and the plugin together: both sides of the device seam now reject payloads that
0.5.0 accepted.

### Changed

- **Breaking**: the pairing, local-access and upload-acknowledgement models reject unknown
  fields.
- **Breaking**: device fingerprints, JWK coordinates, relay header values and relay paths must
  be printable ASCII, and JWK `x` and `y` must be 42 to 64 base64url characters.
- The package is now licensed Apache-2.0 instead of AGPL-3.0-or-later, so code that imports
  these models does not take on the plugin's copyleft.

### Added

- `PAIRING_TTL_CEILING_SECONDS`, the upper bound for `PairingRegisterResponse.expires_in`.

### Fixed

- `CameraProperties.color_temperature` reads libcamera's `ColourTemperature` control. It was
  always `None` before, because the field only accepted the `ColorTemperature` spelling.
- The libcamera links in the `ImageProperties` and `CameraProperties` docstrings point to
  the control and property definitions again.

[0.5.2]: https://github.com/CMLPlatform/relab-rpi-cam-plugin/compare/relab-rpi-cam-models-v0.5.0...relab-rpi-cam-models-v0.5.2
[unreleased]: https://github.com/CMLPlatform/relab-rpi-cam-plugin/compare/relab-rpi-cam-models-v0.5.2...HEAD
