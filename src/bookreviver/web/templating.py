"""Jinja2 environment shared by all routers."""

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from bookreviver.stages import Stage

WEB_DIR: Path = Path(__file__).parent
STATIC_DIR: Path = WEB_DIR / 'static'


def _stage_context(_request: Request) -> dict[str, Any]:
    """Expose the pipeline stages to every template, for the stage tabs."""
    return {'stages': list(Stage)}


templates = Jinja2Templates(directory=WEB_DIR / 'templates', context_processors=[_stage_context])
