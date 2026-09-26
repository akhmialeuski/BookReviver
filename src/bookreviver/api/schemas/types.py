"""Constrained types reused by request schemas and parameters, declared once so every endpoint applies one rule."""

from typing import Annotated

from pydantic import Field, StringConstraints

TITLE_MAX_LENGTH: int = 500
TEXT_MAX_LENGTH: int = 300
NOTES_MAX_LENGTH: int = 10_000
ASSET_KEY_MAX_LENGTH: int = 1_024
# Slash-separated names that never start with a dot, so a key cannot climb out of its directory; commas appear in
# IIIF tile paths such as ``0,0,512,512/512,/0/default.jpg``
ASSET_KEY_PATTERN: str = r'^[\w,-][\w.,-]*(/[\w,-][\w.,-]*)*$'

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=TITLE_MAX_LENGTH)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=TEXT_MAX_LENGTH)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=NOTES_MAX_LENGTH)]
PageIndex = Annotated[int, Field(ge=0, description='Position of the page in the book, counted from zero')]
AssetKey = Annotated[str, StringConstraints(max_length=ASSET_KEY_MAX_LENGTH, pattern=ASSET_KEY_PATTERN)]
