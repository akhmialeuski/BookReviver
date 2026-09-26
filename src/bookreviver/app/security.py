"""Security middleware of the API: CSRF protection and rate limiting, installed by the accounts feature."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

    from bookreviver.app.settings import Settings


def install_security(app: FastAPI, settings: Settings) -> None:
    """Add the security middleware to the application; the accounts feature fills this in."""
