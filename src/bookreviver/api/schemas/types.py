"""Constrained types reused by request schemas and parameters, declared once so every endpoint applies one rule."""

from typing import Annotated

from pydantic import StringConstraints

TITLE_MAX_LENGTH: int = 500
TEXT_MAX_LENGTH: int = 300
NOTES_MAX_LENGTH: int = 10_000

# A title is required, so it may not be empty once surrounding whitespace is stripped
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=TITLE_MAX_LENGTH)]
# A short field of a description, such as the authors or the publisher, which may be empty
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=TEXT_MAX_LENGTH)]
# Free-form text, such as the owner's notes, which may be empty
LongText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=NOTES_MAX_LENGTH)]
