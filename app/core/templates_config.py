"""Centralized Jinja2 template configuration with caching enabled."""

from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader

from app.core.config import settings


# Create a single shared Jinja2Templates instance with caching enabled.
# This avoids recreating the environment in each router and improves performance.
def _create_templates() -> Jinja2Templates:
    """Create Jinja2Templates with caching enabled."""
    # Create environment with caching
    loader = FileSystemLoader(settings.templates_path)
    env = Environment(
        loader=loader,
        cache_size=400,  # Cache up to 400 compiled templates
        auto_reload=settings.debug,  # Reload templates in debug mode
        autoescape=True,  # Enable autoescaping for security
    )
    return Jinja2Templates(env=env)


templates = _create_templates()
