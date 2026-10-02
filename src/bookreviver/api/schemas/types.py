"""Constrained types reused by request schemas and parameters, declared once so every endpoint applies one rule."""

from typing import TYPE_CHECKING, Annotated

from pydantic import AfterValidator, Field, StringConstraints

from bookreviver.domain.entities import VERSION_ID_PATTERN
from bookreviver.domain.ids import PageId, PageVersionId

if TYPE_CHECKING:
    from collections.abc import Hashable, Sequence

TITLE_MAX_LENGTH: int = 500
TEXT_MAX_LENGTH: int = 300
NOTES_MAX_LENGTH: int = 10_000
IDENTIFIER_MAX_LENGTH: int = 2_048
LANGUAGE_CODE_PATTERN: str = r'^[a-z]{3}$'
HEIGHT_CM_MIN: int = 1
HEIGHT_CM_MAX: int = 200
# Longest lists a description accepts, so one request cannot turn the row of a project into megabytes of JSON
CONTRIBUTORS_MAX_LENGTH: int = 50
IDENTIFIERS_MAX_LENGTH: int = 20
LANGUAGES_MAX_LENGTH: int = 10
SUBJECTS_MAX_LENGTH: int = 50
TITLES_MAX_LENGTH: int = 10
REPEATED_ITEMS: str = 'The list may not repeat an item.'
# Longest printed number of a page, such as ``[xii]`` or ``12a``
PAGE_LABEL_MAX_LENGTH: int = 50
# Largest side of a generated blank leaf in pixels and largest resolution to record in it, which bound the image a
# request can make the worker write
PAGE_SIDE_MAX_PX: int = 30_000
DPI_MAX: float = 4_800.0
# Most pages one request moves or numbers, which bounds the rows it writes
PAGE_BATCH_MAX_LENGTH: int = 2_000
ASSET_KEY_MAX_LENGTH: int = 1_024
# Slash-separated names that never start with a dot, so a key cannot climb out of its directory; commas appear in
# IIIF tile paths such as ``0,0,512,512/512,/0/default.jpg``
ASSET_KEY_PATTERN: str = r'^[\w,-][\w.,-]*(/[\w,-][\w.,-]*)*$'

# A title is required, so it may not be empty once surrounding whitespace is stripped
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=TITLE_MAX_LENGTH)]
# A short field of a description, such as the authors or the publisher, which may be empty
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=TEXT_MAX_LENGTH)]
# Free-form text, such as the owner's notes, which may be empty
LongText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=NOTES_MAX_LENGTH)]
# The storage key of a derived file, as found in the addresses of a page or a scan
AssetKey = Annotated[str, StringConstraints(max_length=ASSET_KEY_MAX_LENGTH, pattern=ASSET_KEY_PATTERN)]
# The name of a contributor as printed, which is checked for length alone because the forms of names in old books
# are too varied for a rule
PersonName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=TEXT_MAX_LENGTH)]
# The value of an identifier as a person writes it, which its scheme then checks and normalizes
IdentifierValue = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=IDENTIFIER_MAX_LENGTH)
]
# The ISO 639-3 code of a language, such as ``rus`` or ``bel``
LanguageCode = Annotated[str, StringConstraints(pattern=LANGUAGE_CODE_PATTERN)]
# The height of a book in centimetres
HeightCm = Annotated[int, Field(ge=HEIGHT_CM_MIN, le=HEIGHT_CM_MAX)]
# One title or one subject of a list, which is never empty
ListedText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=TEXT_MAX_LENGTH)]


def _refuse_repeats[ItemT: Hashable](items: Sequence[ItemT]) -> Sequence[ItemT]:
    """Refuse a list that holds the same item twice.

    :param items: The items of the list.
    :type items: Sequence[ItemT]
    :returns: The items unchanged.
    :rtype: Sequence[ItemT]
    :raises ValueError: If an item appears more than once.
    """
    if len(set(items)) != len(items):
        raise ValueError(REPEATED_ITEMS)
    return items


# The languages of a book without repeats
LanguageList = Annotated[list[LanguageCode], Field(max_length=LANGUAGES_MAX_LENGTH), AfterValidator(_refuse_repeats)]
SubjectList = Annotated[list[ListedText], Field(max_length=SUBJECTS_MAX_LENGTH)]
# The printed number of a page, which is empty for a page that has none
PageLabel = Annotated[str, StringConstraints(strip_whitespace=True, max_length=PAGE_LABEL_MAX_LENGTH)]
# One side of a generated blank leaf in pixels, and its resolution in dots per inch
PagePixels = Annotated[int, Field(gt=0, le=PAGE_SIDE_MAX_PX)]
Dpi = Annotated[float, Field(gt=0, le=DPI_MAX)]
# The pages one request acts on, at least one and each at most once
PageIdList = Annotated[
    list[PageId],
    Field(min_length=1, max_length=PAGE_BATCH_MAX_LENGTH),
    AfterValidator(_refuse_repeats),
]
TitleList = Annotated[list[ListedText], Field(max_length=TITLES_MAX_LENGTH)]

# The largest zoom of a canvas as a multiple of the fitted view, and the farthest a centre lies from the view in page
# heights, which bound what a place can hold
CANVAS_ZOOM_MAX: float = 1_000.0
CANVAS_CENTRE_MAX: float = 1_000.0
# Zoom of a canvas, as a multiple of the zoom that fits the view
CanvasZoom = Annotated[float, Field(gt=0, le=CANVAS_ZOOM_MAX, allow_inf_nan=False)]
# A coordinate of the centre of a canvas, in page heights
CanvasCentre = Annotated[float, Field(ge=-CANVAS_CENTRE_MAX, le=CANVAS_CENTRE_MAX, allow_inf_nan=False)]

# The most steps a recipe holds, and the longest key of a processor, which bound what a request stores
RECIPE_STEPS_MAX_LENGTH: int = 50
PROCESSOR_KEY_MAX_LENGTH: int = 100
# The name of a recipe, which is never empty
RecipeName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=TEXT_MAX_LENGTH)]
# The key of a processor, such as ``geometry.deskew``
ProcessorKeyText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=PROCESSOR_KEY_MAX_LENGTH)
]
# The identifier of a page version, a hash cut to 16 hexadecimal digits
VersionIdentifier = Annotated[PageVersionId, StringConstraints(pattern=f'^{VERSION_ID_PATTERN}$')]
