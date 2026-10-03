"""Closed sets of values used across the application, each carrying a human label."""

import enum
import re
from itertools import cycle
from string import ascii_lowercase
from typing import Final, Self
from urllib.parse import urlsplit

from bookreviver.domain.errors import InvalidIdentifierError

# An ISBN without hyphens: ten characters whose last may be the check digit ``X``, or thirteen digits
ISBN_10: Final = re.compile(r'[0-9]{9}[0-9X]')
ISBN_13: Final = re.compile(r'[0-9]{13}')
ISBN_10_MODULUS: Final = 11
ISBN_13_MODULUS: Final = 10
# The check digit ``X`` of an ISBN-10 stands for ten
ISBN_10_X_VALUE: Final = 10
# Weights of the digits of an ISBN-13, which alternate
ISBN_13_WEIGHTS: Final = (1, 3)
# Digits the serial number of an LCCN is padded to after its hyphen
LCCN_SERIAL_LENGTH: Final = 6
# A normalized LCCN has up to four characters of prefix and year, and ends with eight digits
LCCN_NORMALIZED: Final = re.compile(r'[a-z0-9]{0,4}[0-9]{8}')
OCLC_NUMBER: Final = re.compile(r'[0-9]+')
WHITESPACE: Final = re.compile(r'\s')
# Longest shelfmark and longest URL of a copy the domain accepts
SHELFMARK_MAX_LENGTH: Final = 300
URL_MAX_LENGTH: Final = 2_048
URL_SCHEMES: Final = frozenset({'http', 'https'})
# The values and symbols of the Roman numerals in the order they are written, subtractive pairs included, and the
# largest number they can write
ROMAN_NUMERALS: Final = (
    (1000, 'M'),
    (900, 'CM'),
    (500, 'D'),
    (400, 'CD'),
    (100, 'C'),
    (90, 'XC'),
    (50, 'L'),
    (40, 'XL'),
    (10, 'X'),
    (9, 'IX'),
    (5, 'V'),
    (4, 'IV'),
    (1, 'I'),
)
ROMAN_MAX: Final = 3999


class LabeledStrEnum(enum.StrEnum):
    """A string enum whose members are declared as ``(value, label)`` pairs.

    :ivar label: Human-readable name of the member, shown in the interface and in error messages.
    """

    label: str

    def __new__(cls, value: str, label: str = '') -> Self:
        """Create a member whose string value is ``value`` and whose label is ``label``.

        Looking a member up by value, as in ``Stage('import')``, does not call this, so the default only keeps that
        call well-typed.

        :param value: String value of the member, stored and sent over the API.
        :type value: str
        :param label: Human-readable name of the member.
        :type label: str
        :returns: The new member.
        :rtype: Self
        """
        member = str.__new__(cls, value)
        member._value_ = value
        member.label = label
        return member


class Stage(LabeledStrEnum):
    """A step of the digitisation pipeline, in pipeline order."""

    IMPORT = 'import', 'Import'
    PAGE_SPLIT = 'page-split', 'Page split'
    PAGE_ORDER = 'page-order', 'Page order'
    GEOMETRY = 'geometry', 'Geometry'
    CLEANUP = 'cleanup', 'Cleanup'
    LAYOUT = 'layout', 'Layout'
    BACKGROUND = 'background', 'Background'
    RECOGNITION = 'recognition', 'Recognition'
    PROOFREADING = 'proofreading', 'Proofreading'
    TYPESETTING = 'typesetting', 'Typesetting'

    @property
    def position(self) -> int:
        """The place of the stage in the pipeline from zero, by which a stage is earlier or later than another."""
        return list(type(self)).index(self)

    @property
    def manual(self) -> bool:
        """Whether the user does the stage by hand, so it is available whatever plugins are installed."""
        return self in {Stage.IMPORT, Stage.PAGE_ORDER}


class SourceKind(LabeledStrEnum):
    """What one source of a book is, which selects the format that reads it."""

    PDF = 'pdf', 'PDF document'
    DJVU = 'djvu', 'DjVu document'
    IMAGE = 'image', 'Image file'


class DjvuDocumentKind(LabeledStrEnum):
    """How a DjVu file holds its pages, which decides how many sources it makes."""

    BUNDLED = 'bundled', 'Bundled document, every page in one file'
    INDIRECT = 'indirect', 'Indirect document, an index file with one file per page'
    SINGLE_PAGE = 'single-page', 'Single-page file'


class ColorMode(LabeledStrEnum):
    """Colour depth of a page image as stored in the source."""

    BILEVEL = 'bilevel', 'Black and white'
    GRAY = 'gray', 'Grayscale'
    COLOR = 'color', 'Colour'
    UNKNOWN = 'unknown', 'Unknown'


class Orthography(LabeledStrEnum):
    """Spelling norm of the printed text."""

    UNKNOWN = 'unknown', 'Unknown'
    PRE_REFORM = 'pre-reform', 'Pre-reform'
    MODERN = 'modern', 'Modern'


class Script(LabeledStrEnum):
    """Writing system of the printed text, which is separate from its spelling norm."""

    UNKNOWN = 'unknown', 'Unknown'
    CYRILLIC = 'cyrillic', 'Cyrillic'
    LATIN = 'latin', 'Latin'
    MIXED = 'mixed', 'Mixed'


class RightsStatus(LabeledStrEnum):
    """Whether the result of the work on a book may be published."""

    UNKNOWN = 'unknown', 'Unknown'
    PUBLIC_DOMAIN = 'public-domain', 'Public domain'
    IN_COPYRIGHT = 'in-copyright', 'In copyright'


class ContributorRole(LabeledStrEnum):
    """Role of a person in the making of a book.

    The value of a member is its code in the `MARC Code List for Relators <https://www.loc.gov/marc/relators/>`_ and
    the label is the term of that list, so an export to library formats writes the codes without a translation table.
    """

    AUTHOR = 'aut', 'author'
    EDITOR = 'edt', 'editor'
    COMPILER = 'com', 'compiler'
    TRANSLATOR = 'trl', 'translator'
    ILLUSTRATOR = 'ill', 'illustrator'
    ENGRAVER = 'egr', 'engraver'
    LITHOGRAPHER = 'ltg', 'lithographer'
    PHOTOGRAPHER = 'pht', 'photographer'
    WRITER_OF_PREFACE = 'wpr', 'writer of preface'
    WRITER_OF_INTRODUCTION = 'win', 'writer of introduction'
    ANNOTATOR = 'ann', 'annotator'
    COMMENTATOR = 'cmm', 'commentator'
    DEDICATEE = 'dte', 'dedicatee'
    CONTRIBUTOR = 'ctb', 'contributor'
    OTHER = 'oth', 'other'


class IdentifierScheme(LabeledStrEnum):
    """Kind of number or address that identifies a book or one copy of it, each with its rule of writing."""

    ISBN = 'isbn', 'ISBN'
    OCLC = 'oclc', 'OCLC number'
    LCCN = 'lccn', 'LCCN'
    SHELFMARK = 'shelfmark', 'Shelfmark'
    URL = 'url', 'URL of a copy'

    def normalize(self, raw: str) -> str:
        """Return ``raw`` in the one form the scheme compares by, after checking it.

        The rules follow the standards of each scheme: an ISBN loses its hyphens and spaces and must carry a correct
        check digit, an OCLC number is digits, an LCCN is normalized as the Library of Congress describes, a shelfmark
        is free text, and a URL is an ``http`` or ``https`` address.

        :param raw: The identifier as a person wrote it or a file stored it.
        :type raw: str
        :returns: The identifier in normalized form.
        :rtype: str
        :raises InvalidIdentifierError: If the identifier breaks the rules of its scheme.
        """
        text = raw.strip()
        match self:
            case IdentifierScheme.ISBN:
                normalized = re.sub(r'[-\s]', '', text).upper()
                valid = self._has_isbn_check_digit(normalized)
            case IdentifierScheme.OCLC:
                normalized = text
                valid = OCLC_NUMBER.fullmatch(normalized) is not None
            case IdentifierScheme.LCCN:
                normalized = WHITESPACE.sub('', text).lower().partition('/')[0]
                head, hyphen, serial = normalized.partition('-')
                if hyphen:
                    normalized = head + serial.zfill(LCCN_SERIAL_LENGTH)
                valid = LCCN_NORMALIZED.fullmatch(normalized) is not None
            case IdentifierScheme.SHELFMARK:
                normalized = text
                valid = 0 < len(text) <= SHELFMARK_MAX_LENGTH
            case IdentifierScheme.URL:
                normalized = text
                valid = self._is_web_address(text)
        if not valid:
            err_msg = f'{raw!r} is not a valid {self.label}.'
            raise InvalidIdentifierError(err_msg)
        return normalized

    @staticmethod
    def _has_isbn_check_digit(isbn: str) -> bool:
        """Tell whether ``isbn`` is an ISBN-10 or an ISBN-13 whose weighted digit sum is a multiple of its modulus.

        :param isbn: The ISBN without hyphens and spaces, in upper case.
        :type isbn: str
        :returns: Whether it has the length, the digits and the check digit of an ISBN.
        :rtype: bool
        """
        if ISBN_10.fullmatch(isbn):
            values = [ISBN_10_X_VALUE if digit == 'X' else int(digit) for digit in isbn]
            weights = range(len(isbn), 0, -1)
            return sum(weight * value for weight, value in zip(weights, values, strict=True)) % ISBN_10_MODULUS == 0
        if ISBN_13.fullmatch(isbn):
            total = sum(weight * int(digit) for weight, digit in zip(cycle(ISBN_13_WEIGHTS), isbn, strict=False))
            return total % ISBN_13_MODULUS == 0
        return False

    @staticmethod
    def _is_web_address(text: str) -> bool:
        """Tell whether ``text`` is a single ``http`` or ``https`` address with a host.

        :param text: The address without surrounding whitespace.
        :type text: str
        :returns: Whether it is such an address.
        :rtype: bool
        """
        if not 0 < len(text) <= URL_MAX_LENGTH or WHITESPACE.search(text):
            return False
        try:
            parts = urlsplit(text)
        except ValueError:
            return False
        return parts.scheme.lower() in URL_SCHEMES and bool(parts.hostname)


class ImagePolicy(LabeledStrEnum):
    """How a project stores the ``full`` image of its scans and page versions.

    A bilevel image is a lossless PNG under either policy. The policy decides only gray and colour images, and a change
    applies to images written afterwards, so nothing stored is encoded again.
    """

    COMPACT = 'compact', 'Compact: gray and colour images as JPEG'
    LOSSLESS = 'lossless', 'Lossless: every image as PNG'

    def full_format(self, color_mode: ColorMode) -> Rendition:
        """Return the format of the ``full`` image of a page of ``color_mode``, by decision 22 of the book model.

        A bilevel page is a 1-bit PNG whatever the policy, since JPEG rings around strokes and a 1-bit PNG is smaller.
        Any other page, one of unknown colour included, is a JPEG under ``compact`` and a PNG under ``lossless``.

        :param color_mode: Colour mode of the page, as its scan reports it.
        :type color_mode: ColorMode
        :returns: ``Rendition.FULL_PNG`` or ``Rendition.FULL_JPEG``.
        :rtype: Rendition
        """
        if color_mode is ColorMode.BILEVEL or self is ImagePolicy.LOSSLESS:
            return Rendition.FULL_PNG
        return Rendition.FULL_JPEG


class PageKind(LabeledStrEnum):
    """Role of a page in the printed book."""

    COVER = 'cover', 'Cover'
    BACK_COVER = 'back-cover', 'Back cover'
    ENDPAPER = 'endpaper', 'Endpaper'
    FRONTISPIECE = 'frontispiece', 'Frontispiece'
    TITLE = 'title', 'Title page'
    TEXT = 'text', 'Text page'
    PLATE = 'plate', 'Plate'
    BLANK = 'blank', 'Blank page'
    OTHER = 'other', 'Other'


class RuleCondition(LabeledStrEnum):
    """What a rule of a stage asks of a page, to give the page the variant of the rule.

    The set is closed: a rule never carries a free-form test. ``GROUP`` is the one condition with an argument, the
    label of the group that the user wrote on the pages. ``ILLUSTRATED`` is declared so that rules and clients can name
    it, but it matches no page yet: the Layout stage, which finds the illustrations of a page, does not exist, and a
    rule on it takes effect the day that stage records them.
    """

    PLATES = 'plates', 'Plates and frontispieces'
    COVERS = 'covers', 'Covers'
    BLANKS = 'blanks', 'Blank pages'
    ILLUSTRATED = 'illustrated', 'Pages with illustrations'
    ODD = 'odd', 'Odd pages'
    EVEN = 'even', 'Even pages'
    GROUP = 'group', 'Manual group'

    @property
    def kinds(self) -> frozenset[PageKind]:
        """The kinds of page the condition matches, or none for a condition that does not test the kind."""
        return _KINDS_OF_CONDITION.get(self, frozenset[PageKind]())


# The kinds of page each condition on the kind matches
_KINDS_OF_CONDITION: Final[dict[RuleCondition, frozenset[PageKind]]] = {
    RuleCondition.PLATES: frozenset({PageKind.PLATE, PageKind.FRONTISPIECE}),
    RuleCondition.COVERS: frozenset({PageKind.COVER, PageKind.BACK_COVER}),
    RuleCondition.BLANKS: frozenset({PageKind.BLANK}),
}


class OrderMode(LabeledStrEnum):
    """How strictly the order of the steps of a recipe is kept when the recipe is saved.

    The usual order refuses a step that stands where it cannot work, and the free order lets it stand, with a warning.
    A step that stands off its usual place is never refused, whatever the mode.
    """

    USUAL = 'usual', 'Usual order'
    FREE = 'free', 'Free order'


class OrderRuleKind(LabeledStrEnum):
    """How firmly a processor asks for its place among the steps of a recipe."""

    USUAL = 'usual', 'Usual place'
    REQUIRED = 'required', 'Required place'


class AppliesTo(LabeledStrEnum):
    """The condition of a step of a recipe: which pages the step processes, the others passing it unchanged.

    What makes a page text or a picture is decided here and nowhere else. Until the content of a page is detected, the
    role the user gave the page decides: a plate or a frontispiece is a picture, and every other kind of page is text.
    The colour of a picture is the colour mode of the image the stage starts from, and an unknown mode counts as colour,
    since a step for black and white pictures must not touch a page that may be a colour plate.
    """

    ALL = 'all', 'All pages'
    TEXT = 'text', 'Text pages'
    PICTURES = 'pictures', 'Pictures'
    COLOR_PICTURES = 'color-pictures', 'Colour pictures'
    BW_PICTURES = 'bw-pictures', 'Black-and-white pictures'

    def matches(self, kind: PageKind, color_mode: ColorMode) -> bool:
        """Tell whether a step with this condition processes a page.

        :param kind: Role of the page in the book.
        :type kind: PageKind
        :param color_mode: Colour mode of the image the stage starts from.
        :type color_mode: ColorMode
        :returns: True when the page is processed, False when it passes the step unchanged.
        :rtype: bool
        """
        picture = kind in _PICTURE_KINDS
        match self:
            case AppliesTo.ALL:
                return True
            case AppliesTo.TEXT:
                return not picture
            case AppliesTo.PICTURES:
                return picture
            case AppliesTo.COLOR_PICTURES:
                return picture and color_mode in {ColorMode.COLOR, ColorMode.UNKNOWN}
            case AppliesTo.BW_PICTURES:
                return picture and color_mode in {ColorMode.BILEVEL, ColorMode.GRAY}


# The kinds of page that are pictures, which are those a rule on plates sends to the plates recipe
_PICTURE_KINDS: Final[frozenset[PageKind]] = RuleCondition.PLATES.kinds


class PageOrigin(LabeledStrEnum):
    """Where the image of a page comes from."""

    SCAN = 'scan', 'Copy of a part of a scan'
    BLANK = 'blank', 'Generated blank leaf'
    PLACEHOLDER = 'placeholder', 'Placeholder waiting for a scan'


class BlankFill(LabeledStrEnum):
    """What the image of a page of kind blank is: the scan the page was cut from, or a leaf made in its place.

    A leaf is a page the program draws, so the steps of the stages after the page order pass it unchanged. The scan of
    the page is kept whatever the choice, so the choice can be undone.
    """

    SCAN = 'scan', 'Keep the scan'
    WHITE = 'white', 'White leaf'
    PAPER = 'paper', 'Paper of the book'

    @property
    def paper_fill(self) -> PaperFill:
        """The fill ``pages.blank`` draws a leaf of this choice with.

        :raises ValueError: If the choice is the scan, which no leaf is drawn for.
        """
        if self is BlankFill.SCAN:
            err_msg = 'A page that keeps its scan has no leaf to fill.'
            raise ValueError(err_msg)
        return PaperFill(self.value)


class LabelStyle(LabeledStrEnum):
    """How the number of a page is written, which the numbering of a range of pages applies to its numbers.

    The domain imports only the standard library, so the Roman numerals are written here and not taken from the
    ``roman`` package, whose one function would be about a dozen lines of ours.
    """

    ARABIC = 'arabic', 'Arabic'
    ROMAN_LOWER = 'roman-lower', 'Roman, lower case'
    ROMAN_UPPER = 'roman-upper', 'Roman, upper case'
    ALPHA_LOWER = 'alpha-lower', 'Letters, lower case'
    ALPHA_UPPER = 'alpha-upper', 'Letters, upper case'
    NONE = 'none', 'No label'

    @property
    def is_roman(self) -> bool:
        """Whether the style writes Roman numerals, which stop at 3999."""
        return self in {LabelStyle.ROMAN_LOWER, LabelStyle.ROMAN_UPPER}

    def write(self, number: int) -> str:
        """Write a page number in this style, which for ``none`` is the empty label that erases a numbering.

        The method is not called ``format``, since that name belongs to ``str``, whose signature it would break. The
        letter styles follow the page labels of PDF: ``a`` to ``z``, then ``aa`` to ``zz``, then ``aaa``.

        :param number: Number of the page, from 1.
        :type number: int
        :returns: ``12``, ``xii`` or ``XII`` for the number 12, ``b`` or ``B`` for the number 2, and an empty string
                  for ``none``.
        :rtype: str
        :raises ValueError: If the number is below 1, or above 3999 in a Roman style, which has no numeral for it.
        """
        if self is LabelStyle.NONE:
            return ''
        if number < 1 or (self.is_roman and number > ROMAN_MAX):
            err_msg = f'{number} cannot be written as a {self.label.lower()} page number.'
            raise ValueError(err_msg)
        if self is LabelStyle.ARABIC:
            return str(number)
        if self in {LabelStyle.ALPHA_LOWER, LabelStyle.ALPHA_UPPER}:
            repeats, index = divmod(number - 1, len(ascii_lowercase))
            letters = ascii_lowercase[index] * (repeats + 1)
            return letters if self is LabelStyle.ALPHA_LOWER else letters.upper()
        numeral, remaining = '', number
        for value, symbol in ROMAN_NUMERALS:
            repeats, remaining = divmod(remaining, value)
            numeral += symbol * repeats
        return numeral.lower() if self is LabelStyle.ROMAN_LOWER else numeral


class NumberDisplay(LabeledStrEnum):
    """How the pages of a pagination section show their numbers, and whether they take part in the count.

    A page that is counted but not printed is shown with its number in square brackets, as a bibliographer writes a
    number the book implies and does not print, such as ``[iii]``.
    """

    NOT_COUNTED = 'not-counted', 'Not counted'
    COUNTED = 'counted', 'Counted, not printed'
    PRINTED = 'printed', 'Printed'

    @property
    def counts(self) -> bool:
        """Whether a page of the section takes a number and so moves the count on."""
        return self is not NumberDisplay.NOT_COUNTED


class Side(LabeledStrEnum):
    """Which side of a page in the book a neighbour or a new position lies on."""

    BEFORE = 'before', 'Before the page'
    AFTER = 'after', 'After the page'


class SheetEdge(LabeledStrEnum):
    """A side of a sheet of paper, named as the reader sees the upright page."""

    TOP = 'top', 'Top'
    RIGHT = 'right', 'Right'
    BOTTOM = 'bottom', 'Bottom'
    LEFT = 'left', 'Left'


class Binarization(LabeledStrEnum):
    """How a page is made black and white to find its ink."""

    OTSU = 'otsu', 'One threshold for the page, found by the method of Otsu'
    ADAPTIVE = 'adaptive', 'A threshold for each neighbourhood, for a page lit unevenly'


class PageSide(LabeledStrEnum):
    """Which side of an open book a page lies on, which its place in the book decides."""

    LEFT = 'left', 'Left page'
    RIGHT = 'right', 'Right page'

    @classmethod
    def of_position(cls, position: int) -> PageSide:
        """Give the side of the page at a place in the book, an odd place being a right page, as in the viewer.

        :param position: Place of the page in the book counted from 1.
        :type position: int
        :returns: The side.
        :rtype: PageSide
        """
        return cls.RIGHT if position % 2 == 1 else cls.LEFT


class VerticalAlign(LabeledStrEnum):
    """Where a block of text stands between the top and the bottom margin of a page."""

    TOP = 'top', 'By the top margin'
    CENTER = 'center', 'In the middle'
    BOTTOM = 'bottom', 'By the bottom margin'


class HorizontalAlign(LabeledStrEnum):
    """Where a block of text stands between the side margins of a page."""

    INNER = 'inner', 'By the margin at the gutter'
    OUTER = 'outer', 'By the margin at the outer edge'
    LEFT = 'left', 'By the left margin'
    RIGHT = 'right', 'By the right margin'
    CENTER = 'center', 'In the middle'


class MarginsBy(LabeledStrEnum):
    """How the side margins of a page are told apart."""

    INNER_OUTER = 'inner-outer', 'Inner and outer, by the side of the page in the book'
    LEFT_RIGHT = 'left-right', 'Left and right, the same on every page'


class MarginsSource(LabeledStrEnum):
    """Who sets the margins of the step ``geometry.normalize``: the measure of the book or the user."""

    MEASURED = 'measured', 'Measured from the pages of the book'
    MANUAL = 'manual', 'Set by hand'


class NormalizeParam(LabeledStrEnum):
    """Names of the parameters of the step ``geometry.normalize`` that measuring the book reads or writes."""

    MARGINS_SOURCE = 'margins_source', 'Who sets the margins'
    LINE_HEIGHT = 'line_height', 'Distance between the lines of text, in pixels'
    PAGE_WIDTH = 'page_width', 'Width of the page, in pixels'
    PAGE_HEIGHT = 'page_height', 'Height of the page, in pixels'
    MARGIN_TOP = 'margin_top', 'Margin at the top, in pixels'
    MARGIN_BOTTOM = 'margin_bottom', 'Margin at the bottom, in pixels'
    MARGIN_INNER = 'margin_inner', 'Margin at the gutter, in pixels'
    MARGIN_OUTER = 'margin_outer', 'Margin at the outer edge, in pixels'


class BlankParam(LabeledStrEnum):
    """Names of the parameters of the step ``pages.blank`` beyond the size, which a use case writes into a leaf."""

    FILL = 'fill', 'What fills the leaf'
    PAPER_FROM = 'paper_from', 'Versions of the pages the paper is taken from'


class PaperFill(LabeledStrEnum):
    """What colour fills the page round a block of text."""

    PAPER = 'paper', 'The median colour of the paper of the block'
    WHITE = 'white', 'White'


class PerspectiveMethod(LabeledStrEnum):
    """How ``geometry.perspective`` finds the sheet of paper on a scan."""

    PAPER_COLOUR = 'paper-colour', 'By the colour of the paper'
    EDGES = 'edges', 'By the edges of the sheet, for paper of the colour of its background'


class DeskewMethod(LabeledStrEnum):
    """How ``geometry.deskew`` finds the angle a page is turned by."""

    PROJECTION = 'projection', 'By the projection of the ink on the rows'
    HOUGH = 'hough', 'By the long straight lines, such as rules and frames'
    BASELINES = 'baselines', 'By the median slope of the baselines of the text'


class CropMethod(LabeledStrEnum):
    """How ``geometry.crop`` finds the frame of the content of a page.

    ``LAYOUT`` is declared so that recipes and clients can name it, but no processor offers it yet: it takes the frame
    from the regions of the Layout stage, which does not exist, so there are no regions for it to read. The day that
    stage records them, ``geometry.crop`` offers the method and nothing declared here changes.
    """

    INK_BLOCKS = 'ink-blocks', 'By the blocks of ink'
    LAYOUT = 'layout', 'By the regions of the Layout stage'


class DewarpMethod(LabeledStrEnum):
    """How ``geometry.dewarp`` finds the bend of a page.

    ``DOCRES`` is declared so that recipes and clients can name it, but no processor offers it: its model needs a
    graphics card, which the plugin of the optional group ``gpu`` brings, and that plugin does not exist yet.
    """

    TEXT_LINES = 'text-lines', 'By the curves of the lines of text'
    PAGE_EDGES = 'page-edges', 'By the top and bottom edges of the sheet, for pages with few lines'
    UVDOC = 'uvdoc', 'By the UVDoc neural network'
    DOCRES = 'docres', 'By the DocRes neural network'


class OutputMode(LabeledStrEnum):
    """What the page looks like after ``cleanup.binarize``."""

    BW = 'bw', 'Black and white'
    GRAY = 'gray', 'Grayscale with even light'
    COLOR = 'color', 'Colour with even light'
    MIXED = 'mixed', 'Black and white text, pictures in tones'


class BinarizationMethod(LabeledStrEnum):
    """How ``cleanup.binarize`` parts the ink of a page from its paper.

    ``NEURAL`` is declared for the plugin of a model that comes in the ``gpu`` group, and no step offers it yet.
    """

    OTSU = 'otsu', 'Otsu, one threshold for the page'
    SAUVOLA = 'sauvola', 'Sauvola, a threshold for each neighbourhood'
    WOLF = 'wolf', 'Wolf, Sauvola with the contrast of the page'
    ISAUVOLA = 'isauvola', 'ISauvola, Sauvola for a stained page'
    SU = 'su', 'Su, the edges of the strokes'
    GATOS = 'gatos', 'Gatos, a background surface under the ink'
    NICK = 'nick', 'NICK, for pages of pale ink'
    BRADLEY = 'bradley', 'Bradley, the mean of the neighbourhood'
    NEURAL = 'neural', 'Neural network'


class EraserFill(LabeledStrEnum):
    """What ``cleanup.eraser`` paints the erased area with."""

    WHITE = 'white', 'White'
    BLACK = 'black', 'Black'
    AROUND = 'around', 'The mean colour around the area'


class ZoneMode(LabeledStrEnum):
    """What a zone of the ``regions`` editor does to the picture zones a step found."""

    ADD = 'add', 'Picture'
    REMOVE = 'remove', 'Not a picture'


class PlaceMode(LabeledStrEnum):
    """Where in the interface a reader left a book."""

    WORKSPACE = 'workspace', 'Workspace of a stage'
    READING = 'reading', 'Reading mode'


class ViewMode(LabeledStrEnum):
    """How the canvas lays the pages of a book out."""

    PAGE = 'page', 'One page'
    SPREAD = 'spread', 'Two-page spread'
    GRID = 'grid', 'Grid of many pages'


class CompareMode(LabeledStrEnum):
    """How the result of a stage is compared with the result before it."""

    OFF = 'off', 'No comparison'
    SWIPE = 'swipe', 'Swipe over the page'
    SIDE = 'side', 'Side by side'


class PageFilter(LabeledStrEnum):
    """Which pages the strip and the grid of a stage list."""

    ALL = 'all', 'All pages'
    CHECK = 'check', 'Pages to check'
    LEFT_OUT = 'left-out', 'Pages left out of the book'
    WIDE = 'wide', 'Pages cut from a wide scan'


class PageChange(LabeledStrEnum):
    """What a use case did to the pages of a book, which the ``PagesChanged`` event reports."""

    MOVED = 'moved', 'Moved'
    EDITED = 'edited', 'Edited'
    ADDED = 'added', 'Added'
    REMOVED = 'removed', 'Removed'


class NewPageOrigin(LabeledStrEnum):
    """Where the image of a page the user adds comes from.

    Only a blank leaf and a placeholder are added by hand, since a page cut from a scan is made by the page split and
    by binding a scan to a placeholder.
    """

    BLANK = 'blank', 'Generated blank leaf'
    PLACEHOLDER = 'placeholder', 'Placeholder waiting for a scan'

    @property
    def page_origin(self) -> PageOrigin:
        """The origin of the page this one makes."""
        return PageOrigin(self.value)


class VersionData(LabeledStrEnum):
    """Keys of the data of a version: the size of its image, what its step found, and why it failed."""

    WIDTH_PX = 'width_px', 'Width of the image in pixels'
    HEIGHT_PX = 'height_px', 'Height of the image in pixels'
    DPI = 'dpi', 'Resolution of the image in dots per inch'
    ERROR = 'error', 'Why the version could not be made'
    ANGLE = 'angle', 'Angle a page was turned by, in degrees'
    CONFIDENCE = 'confidence', 'How sure the step is of what it found, from 0 to 1'
    SKIPPED = 'skipped', 'Whether the step left the image as it was'
    SKIPPED_BY_CONDITION = 'skipped_by_condition', 'Whether the page did not meet the condition of the step'
    OVERLAP_PX = 'overlap_px', 'Width in pixels a half of a spread reaches over the cut'
    CUT_X = 'cut_x', 'Place of the cut in the scan, as the distance in pixels from its left edge'
    CUT_TOP_X = 'cut_top_x', 'Place of the cut at the top row of the scan, in pixels from its left edge'
    CUT_BOTTOM_X = 'cut_bottom_x', 'Place of the cut at the bottom row of the scan, in pixels from its left edge'
    PAGES = 'pages', 'Number of pages the scan was split into, one or two'
    SOURCE_WIDTH_PX = 'source_width_px', 'Width in pixels of the full image the edit of the step is drawn on'
    SOURCE_HEIGHT_PX = (
        'source_height_px',
        'Height in pixels of the full image the edit of the step is drawn on',
    )
    LINE_HEIGHT_PX = 'line_height_px', 'Median distance between the lines of text, in the pixels of the full image'
    QUAD = 'quad', 'Corners of the sheet the step found in its input, in the pixels of the full image'
    FRAME = 'frame', 'Frame of the content the step found in its input, in the pixels of the full image'
    CUT_EDGES = 'cut_edges', 'Sides of the sheet that lie on the edge of the scan, where the paper was cut'
    REVIEW = 'review', 'Reason an earlier step of the recipe marked the page for a second look'
    LINES = 'lines', 'Number of lines of text or edges of the sheet a dewarping followed'
    BEND = 'bend', 'How far the lines of the page were bent, in pixels for each thousand of its width'
    RESIDUAL = 'residual', 'How far the lines still deviate from straight after dewarping, in pixels per thousand'
    MESH = 'mesh', 'The nodes of the curves a dewarping followed, in the pixels of the full image, for its editor'
    CONTENT_FRAME = 'content_frame', 'Frame of the content in the pixels of the image of this version'
    METHOD = 'method', 'Method that parted the ink from the paper'
    MODE = 'mode', 'What the page was made into'
    THRESHOLD = 'threshold', 'Threshold of the page when the method has one for the whole page'
    ZONES = 'zones', 'Picture zones of the page in the pixels of the full image the step read'
    SPECKS = 'specks', 'Number of specks the step removed'


class VersionState(LabeledStrEnum):
    """Lifecycle of a page version, from its creation to its result."""

    PENDING = 'pending', 'Pending'
    RUNNING = 'running', 'Running'
    READY = 'ready', 'Ready'
    FAILED = 'failed', 'Failed'


class StageState(LabeledStrEnum):
    """Whether the current version of a stage of a page still matches the inputs the stage would run on."""

    FRESH = 'fresh', 'Up to date'
    STALE = 'stale', 'Out of date'
    FAILED = 'failed', 'Failed'


class PageStageStatus(LabeledStrEnum):
    """Where one page stands in one stage: the state of its record, or that the stage has not run on it yet."""

    NOT_RUN = 'not-run', 'Not processed'
    FRESH = 'fresh', 'Up to date'
    STALE = 'stale', 'Out of date'
    FAILED = 'failed', 'Failed'

    @classmethod
    def of(cls, state: StageState | None) -> PageStageStatus:
        """Give the status of a page from the state of its record of a stage.

        :param state: State of the record, or None for a page the stage has no record of.
        :type state: StageState | None
        :returns: The status, which is not run for a page without a record.
        :rtype: PageStageStatus
        """
        return cls.NOT_RUN if state is None else cls(state.value)


class StageStatus(LabeledStrEnum):
    """Where a whole stage stands in a book, summed over its pages."""

    DONE = 'done', 'Done'
    ATTENTION = 'attention', 'Needs a look'
    RUNNING = 'running', 'Running'
    WAITING = 'waiting', 'Waiting'
    UNAVAILABLE = 'unavailable', 'Not available yet'


class ReviewReason(LabeledStrEnum):
    """Why a processed page is marked for a second look, though its step finished without an error."""

    LOW_CONFIDENCE = 'low-confidence', 'The step was not sure of its result'
    NOT_APPLIED = 'not-applied', 'The step left the page as it was, because it was not sure'
    UNSURE_GUTTER = 'unsure-gutter', 'The gutter of the spread was not found for certain'
    NARROW_GUTTER = 'narrow-gutter', 'Narrow scan with a gutter in the middle'
    CUT_BY_EDGE = 'cut-by-edge', 'Text may be cut by the edge of the scan'
    TEXT_SIZE = 'size-differs', 'The text of this page differs too much in size'
    FEW_LINES = 'few-lines', 'Too few lines of text were found to tell how the page is bent'
    HIGH_RESIDUAL = 'high-residual', 'The lines of the page are still bent after dewarping'


class RunOutcome(LabeledStrEnum):
    """What a run of a recipe came to on one page."""

    DONE = 'done', 'Processed'
    SKIPPED = 'skipped', 'Skipped, the page has no image to process'
    FAILED = 'failed', 'Failed'


class VersionScale(LabeledStrEnum):
    """The size of the image a step ran on, which tells a full run from a preview of its parameters."""

    FULL = 'full', 'Full image'
    PREVIEW = 'preview', 'Preview image'


class ProcessorScope(LabeledStrEnum):
    """How many outputs a processor makes from its input."""

    PAGE = 'page', 'One output for the page'
    SPLIT = 'split', 'One output for each part of a scan'


class VersionOutput(LabeledStrEnum):
    """What a processing step writes."""

    IMAGE = 'image', 'Page image'
    MASK = 'mask', 'Mask'
    REGIONS = 'regions', 'Regions'
    TEXT = 'text', 'Text'


class StepField(LabeledStrEnum):
    """Keys of a step of a recipe in the JSON it is stored as, in a recipe row and in the parameters of a job."""

    PROCESSOR_KEY = 'processor_key', 'Key of the processor that runs the step'
    PARAMS = 'params', 'Parameters the processor runs with'
    ENABLED = 'enabled', 'Whether a run and a preview run the step'
    STEP_ID = 'step_id', 'Identifier of the step, which stays as the step moves and is saved'
    APPLIES_TO = 'applies_to', 'Which pages the step processes'


class EditorKind(LabeledStrEnum):
    """The editor a processor offers for the manual edit of its input."""

    NONE = 'none', 'No editor'
    RECT = 'rect', 'Frame'
    QUAD = 'quad', 'Quadrilateral'
    LINE = 'line', 'Line'
    ROTATION = 'rotation', 'Rotation'
    SPLIT = 'split', 'Page split'
    MESH = 'mesh', 'Mesh'
    BRUSH_MASK = 'brush-mask', 'Brush mask'
    REGIONS = 'regions', 'Regions'


class StepLayer(LabeledStrEnum):
    """One of the three layers a page keeps for a step, which a change of the history names."""

    SETTINGS = 'settings', 'Settings of the page'
    FOUND = 'found', 'Found by the automatic run'
    HAND = 'hand', 'Set by hand'


class ChangeSource(LabeledStrEnum):
    """What made a change of a layer of a step on a page."""

    USER = 'user', 'The user'
    RUN = 'run', 'A run'
    CARRY_OVER = 'carry-over', 'A carry-over from another page'
    RESET = 'reset', 'A reset to the defaults'


class TransformKind(LabeledStrEnum):
    """Kind of the coordinate transform a processing step applies from its input to its output."""

    IDENTITY = 'identity', 'Unchanged coordinates'
    CROP = 'crop', 'Crop to a quadrilateral'
    ROTATE = 'rotate', 'Rotation by an angle'
    PERSPECTIVE = 'perspective', 'Perspective correction of a quadrilateral'
    SCALE = 'scale', 'Scaling by a factor'
    MESH = 'mesh', 'Dewarping along a stored mesh'
    PLACE = 'place', 'Scaling and placing a block of text on a page'


class JobKind(LabeledStrEnum):
    """What a background job does."""

    IMPORT_SOURCE = 'import-source', 'Import source'
    PREPARE_PAGES = 'prepare-pages', 'Prepare pages'
    RUN_STAGE = 'run-stage', 'Run a stage'
    PREVIEW_STEP = 'preview-step', 'Preview a step'
    CUT_TILES = 'cut-tiles', 'Cut tiles'
    COLLECT_VERSIONS = 'collect-versions', 'Collect old versions'
    MEASURE_BOOK = 'measure-book', 'Measure the book'

    @classmethod
    def processing(cls) -> frozenset[JobKind]:
        """Return the kinds of job that read and write the versions of pages, of which a project runs one at a time.

        A run, a preview and a tile cutting write the files of versions they may have found made already, and a
        collection deletes them, so no two of them may overlap, and two of one kind would write the same files. A
        measure of the book reads the versions a run writes and rewrites the parameters a run reads, so it is one of
        them.

        :returns: The kinds of the processing jobs.
        :rtype: frozenset[JobKind]
        """
        return cls.requested() | cls.housekeeping()

    @classmethod
    def requested(cls) -> frozenset[JobKind]:
        """Return the kinds of processing job that the user asks for, of which a project has one queued or running.

        :returns: A run, a preview and a measure of the book.
        :rtype: frozenset[JobKind]
        """
        return frozenset({cls.RUN_STAGE, cls.PREVIEW_STEP, cls.MEASURE_BOOK})

    @classmethod
    def housekeeping(cls) -> frozenset[JobKind]:
        """Return the kinds of processing job that the application queues for itself, of which a project has one.

        A job of these kinds never refuses what the user asks for. The request is stored and waits, and starts when
        the housekeeping job ends.

        :returns: A tile cutting and a collection of old versions.
        :rtype: frozenset[JobKind]
        """
        return frozenset({cls.CUT_TILES, cls.COLLECT_VERSIONS})


class JobState(LabeledStrEnum):
    """Lifecycle of a background job."""

    QUEUED = 'queued', 'Queued'
    RUNNING = 'running', 'Running'
    SUCCEEDED = 'succeeded', 'Succeeded'
    FAILED = 'failed', 'Failed'
    CANCELLED = 'cancelled', 'Cancelled'

    @property
    def is_final(self) -> bool:
        """Whether the job will not change state any more."""
        return self in {JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED}

    @classmethod
    def active(cls) -> frozenset[JobState]:
        """Return the states of a job that has not finished, which a worker or a cancellation may still leave.

        :returns: Every state that is not final.
        :rtype: frozenset[JobState]
        """
        return frozenset(state for state in cls if not state.is_final)


class WorkerPool(LabeledStrEnum):
    """Class of worker a job needs, which routes it to the right queue."""

    CPU = 'cpu', 'CPU'
    GPU = 'gpu', 'GPU'
    LLM = 'llm', 'Language model'


class UploadProblem(LabeledStrEnum):
    """Why an upload cannot become a source."""

    NO_FILES = 'no-files', 'Choose PDF files, DjVu files or page images to upload.'
    EMPTY_NAME = 'empty-name', 'Every uploaded file needs a name.'
    UNSAFE_PATH = 'unsafe-path', 'An uploaded file has a path that leaves its folder, such as an absolute path or ..'
    DUPLICATE_NAME = 'duplicate-name', 'Two uploaded files have the same path.'
    UNSUPPORTED_TYPE = 'unsupported-type', 'Only PDF, DjVu, TIFF, JPEG, JPEG 2000 and PNG files are accepted.'
    TOO_LARGE = 'too-large', 'The upload is larger than the allowed size.'
    TOO_MANY_FILES = 'too-many-files', 'The upload has more files than allowed.'


class RejectionReason(LabeledStrEnum):
    """Why one file of an upload was not imported, while the other files of the upload were."""

    DUPLICATE = 'duplicate', 'The project already has this file.'
    UNREADABLE = 'unreadable', 'The file cannot be read as a source.'
    UNSUPPORTED_TYPE = 'unsupported-type', 'The type of the file is not accepted as a source.'
    SYSTEM_FILE = 'system-file', 'The file is a system file of the operating system, not a part of the book.'


class SystemFile(LabeledStrEnum):
    """A file an operating system adds to a folder, which a directory upload carries along but is not a book source.

    The value of a member is its name in lower case, and ``APPLE_DOUBLE`` is a prefix, since macOS writes the resource
    fork of ``001.tif`` as ``._001.tif`` and such a file has the suffix of the image it accompanies.
    """

    THUMBS_DB = 'thumbs.db', 'Windows thumbnail cache'
    DESKTOP_INI = 'desktop.ini', 'Windows folder settings'
    DS_STORE = '.ds_store', 'macOS folder settings'
    APPLE_DOUBLE = '._', 'macOS resource fork of another file'

    @classmethod
    def matches(cls, name: str) -> bool:
        """Tell whether a file is a system file, by the last segment of its name in any letter case.

        :param name: File name, or a relative path whose last segment is the file name, with a slash or a backslash
                     between segments.
        :type name: str
        :returns: Whether the name is one of the known system files, or starts with the prefix of one.
        :rtype: bool
        """
        folded = name.replace('\\', '/').rsplit('/', 1)[-1].casefold()
        return any(
            folded.startswith(member.value) if member is cls.APPLE_DOUBLE else folded == member.value for member in cls
        )


class FileType(LabeledStrEnum):
    """A file type accepted as a book source."""

    PDF = 'pdf', 'PDF'
    DJVU = 'djvu', 'DjVu'
    TIFF = 'tiff', 'TIFF'
    JPEG = 'jpeg', 'JPEG'
    JPEG_2000 = 'jpeg-2000', 'JPEG 2000'
    PNG = 'png', 'PNG'

    @property
    def suffixes(self) -> frozenset[str]:
        """File name suffixes of this type, lower case with the dot."""
        return FILE_TYPE_SUFFIXES[self]

    @property
    def source_kind(self) -> SourceKind:
        """The kind of source a file of this type makes."""
        match self:
            case FileType.PDF:
                return SourceKind.PDF
            case FileType.DJVU:
                return SourceKind.DJVU
            case _:
                return SourceKind.IMAGE

    @classmethod
    def from_name(cls, name: str) -> FileType | None:
        """Return the type of a file by its name, or None when the suffix is not accepted.

        :param name: File name, compared by its suffix in any letter case.
        :type name: str
        :returns: The accepted file type with this suffix, or None.
        :rtype: FileType | None
        """
        suffix = name[name.rfind('.') :].lower() if '.' in name else ''
        return next((file_type for file_type in cls if suffix in file_type.suffixes), None)


FILE_TYPE_SUFFIXES: dict[FileType, frozenset[str]] = {
    FileType.PDF: frozenset({'.pdf'}),
    FileType.DJVU: frozenset({'.djvu', '.djv'}),
    FileType.TIFF: frozenset({'.tif', '.tiff'}),
    FileType.JPEG: frozenset({'.jpg', '.jpeg'}),
    FileType.JPEG_2000: frozenset({'.jp2', '.j2k'}),
    FileType.PNG: frozenset({'.png'}),
}


class Rendition(LabeledStrEnum):
    """One of the derived files of a scan or a page version, named by its value inside their directory.

    ``full`` is a JPEG or a PNG as the project's ``ImagePolicy`` and the colour of the image decide, so it has one
    member per format. ``preview`` and ``thumb`` are always JPEG.
    """

    FULL_JPEG = 'full.jpg', 'Native resolution image as JPEG'
    FULL_PNG = 'full.png', 'Native resolution image as PNG'
    PREVIEW = 'preview.jpg', 'Preview, 2048 px on the longer side'
    THUMBNAIL = 'thumb.jpg', 'Thumbnail'
    TILES = 'iiif', 'IIIF tile pyramid'
    MASK = 'mask.png', 'Mask of the areas a step removed'
    MESH = 'mesh.json', 'Grid of the mesh a dewarping followed'
