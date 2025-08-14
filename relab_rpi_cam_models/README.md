# relab-rpi-cam-models

Shared Pydantic models for the [RELab Raspberry Pi Camera plugin](https://github.com/CMLPlatform/relab-rpi-cam-plugin) and the [RELab platform](https://github.com/CMLPlatform/relab), part of the [CML RELab project](https://cml-relab.org).

## Overview

This package provides pure, hardware-independent data models for camera, image, and stream metadata. It is designed for use in both device-side and platform-side Python projects that interact with the RELab ecosystem.

## Usage

Install from PyPI (or your internal index):

```bash
pip install relab-rpi-cam-models
```

Import models in your code:

```python
from relab_rpi_cam_models.camera import CameraMode, CameraStatusView
from relab_rpi_cam_models.images import ImageMetadata
from relab_rpi_cam_models.stream import StreamView, StreamMode
```

## Features

- Pure Pydantic models (no hardware dependencies)
- Camera, image, and streaming metadata schemas
- Compatible with FastAPI, Pydantic v2, and standard Python

## License

AGPL-3.0-or-later
