# Changelog

## v0.5.0 (2026-07-09)

### Feat

- **observability**: add unauthenticated local-network liveness probe

## v0.4.0 (2026-07-01)

### Feat

- Harden authentication and sessions: enforced lifetimes, ASVS-aligned cookies, same-origin checks for cookie writes, and structured auth, CSRF, and session logging
- Harden browser security: per-request CSP nonces, restricted CORS and private-network access, blocked HTTP TRACE, and limited static file types
- Enforce HTTPS transport for S3 and OTLP endpoints
- Validate relay credentials at startup and boundaries, allowlist backend commands, bound inputs, and rate-limit sensitive actions
- Require auth for pairing, preview, and metrics endpoints, and fail closed for production loopback pairing
- Reject `DEBUG=true` in production and reveal the local API key on demand instead of embedding it in HTML
- Add a security CI workflow, pin container images by digest, and default listeners to loopback
- Add a pairing-state polling endpoint and a client-safe exception handler

### Fix

- Block backslash open-redirects, redact bearer tokens in auth headers, return 403 instead of 500 for non-ASCII API keys, and reject oversized preview thumbnails
- Prevent startup crashes from malformed credential files, and harden capture validation and cleanup
- Fix worker reliability: thermal-throttle desync, preview-sleeper loop death, and preview recovery on fatal HLS errors
- Serialize capture timestamps as UTC-aware and relay binary frames correctly, warn on plain-HTTP base URLs in production, and map dma_heap nodes individually in Compose

### Refactor

- Reorganize into a feature-first layout: image sinks and upload into `delivery`, streaming into `camera/streaming`, metrics into `observability`

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
