# Changelog

## v0.3.0 (2026-04-21)

### Feat

- Add a new local setup and pairing flow, including secure pairing, local connection mode, automatic API key generation, mDNS discovery, pairing status feedback, and improved direct-connection guidance
- Add local camera preview support, including low-resolution snapshots, preview thumbnails, and homepage display of the latest captured image
- Improve the local web UI with better setup flow, responsiveness, theming, and overall usability
- Expand camera and streaming support with updated routes, documentation, HLS activity tracking, and capture-and-store helpers
- Improve runtime reliability with better task lifecycle management, websocket and relay error handling, atomic JPEG encoding, and unpair cleanup
- Add MediaMTX and Docker networking configuration for local API and HLS access
- Add release verification for published package installation

### Refactor

- Reorganize the app into a clearer feature-first structure, modernize the plugin runtime, and simplify related settings and tests

## v0.2.0 (2025-11-26)

### Feat

- Add Dockerized setup for easier deployment
- Add Cloudflare Tunnel support for easy publishing

### Fix

- Add main platform API to default allowed CORS domains
- Improve local setup script
- Ensure the virtual environment is compatible with system Python packages

## v0.1.1 (2025-08-20)

### Fix

- **build**: bumped version to resolve a dependency issue on PyPI, as dependencies were only included after the initial publication.

## v0.1.0 (2025-08-20)

### Feat

- **frontend**: improve frontend access to API
- **logging**: add custom logging setup with file and console output
- **auth**: allow direct broswer-based access via cookies
- **build**: Move to src layout for packaging
- **pre-commit**: Add pre-commit-update hook
- **cicd**: Install commitizen and delete dependabot.yaml

### Fix

- **build**: Only build models package, main plugin app back to root
- **deps**: Custom Renovate config

### Refactor

- **tasks**: improve repeat_task function to handle coroutine tasks and logging
