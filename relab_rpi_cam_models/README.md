# relab-rpi-cam-models

Shared transport contracts for the [RELab Raspberry Pi Camera plugin](https://github.com/CMLPlatform/relab-rpi-cam-plugin) and the [RELab platform](https://github.com/CMLPlatform/relab), part of the [CML RELab project](https://cml-relab.org).

## Overview

This package provides stable, hardware-independent DTOs for camera, image,
stream, and private device-seam payloads exchanged between the plugin and
backend.

Within this repository the package is consumed through the uv workspace, so
plugin changes and contract changes can be developed together without a manual
publish step. Outside this repository, install it from PyPI (or your internal
package index) like any normal Python package.

## Usage

Install from PyPI (or your internal index):

```sh
pip install relab-rpi-cam-models
```

Import models in your code:

```python
from relab_rpi_cam_models.camera import CameraMode, CameraStatusView
from relab_rpi_cam_models.device_seam import PairingPollResponse, RelayCommandEnvelope
from relab_rpi_cam_models.images import ImageMetadata
from relab_rpi_cam_models.stream import StreamView, StreamMode
```

From the repo root, update the workspace lock file after contract changes with:

```sh
uv lock --upgrade relab-rpi-cam-models
```

## Public API

The supported public API is intentionally small:

- `CameraMode`
- `CameraStatusView`
- `StreamMode`
- `StreamView`
- `StreamMetadata`
- `ImageCaptureResponse`
- private backend<->plugin device seam models for pairing, relay, local access,
  and upload acknowledgements
- image metadata DTOs used inside those responses

This package does not own:

- frontend/public API schemas generated from backend OpenAPI
- runtime stream state
- provider-specific request models such as YouTube stream config
- Pillow or EXIF helper behavior
- implementation-specific exceptions
- plugin runtime wiring such as `AppRuntime`, `RuntimeState`, `PairingService`, or `RelayService`

Treat anything outside the documented DTOs as private and subject to change.

## License

AGPL-3.0-or-later
