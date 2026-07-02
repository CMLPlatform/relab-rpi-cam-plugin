"""Centralized Jinja2 template configuration with caching enabled."""

from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader

from app.core.settings import settings

templates = Jinja2Templates(
    env=Environment(
        loader=FileSystemLoader(settings.templates_path),
        cache_size=400,
        auto_reload=settings.debug,
        autoescape=True,
    )
)
