"""Compatibility ASGI entrypoint."""

from app.api.application import app

__all__ = ["app"]
