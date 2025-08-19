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
