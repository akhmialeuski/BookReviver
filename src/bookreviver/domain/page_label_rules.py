"""The numbering of a book written the way an output format keeps it: rules over runs of pages, and printed numbers.

A PDF file keeps page numbers as page label rules, each of which starts at a page and numbers the pages after it in one
style until the next rule. An EPUB file lists the numbers printed on the pages of the paper book. Both are derived from
the pagination sections of a book, and a PDF's rules are read back into sections at import, so the common shape lives
here and the two formats only spell it.
"""

from string import ascii_lowercase
from typing import TYPE_CHECKING, Final

from attrs import field, frozen, validators

from bookreviver.domain.labels import LabeledStrEnum

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


if TYPE_CHECKING:
    from collections.abc import Sequence


@frozen(kw_only=True)
class PageLabelRule:
    """One rule of a run of pages that are numbered in one style, as a PDF keeps it.

    :ivar first_index: Position of the first page of the run among the pages of the file, from 0.
    :ivar style: How the numbers of the run are written; ``none`` writes the prefix alone, which labels a page that has
                 no number of its own, such as ``12a``.
    :ivar prefix: Text written before every number of the run.
    :ivar start: Number of the first page of the run, which a style that writes no number ignores.
    """

    first_index: int = field(validator=validators.ge(0))
    style: LabelStyle
    prefix: str = ''
    start: int = field(default=1, validator=validators.ge(0))

    def fits(self, count: int) -> bool:
        """Tell whether a run of ``count`` pages from this rule is a pagination section.

        A section counts its pages from 1 up, and needs a style that writes numbers, so a rule that starts at 0, a rule
        that writes a prefix alone, and a Roman rule that runs past 3999 pages are not sections.

        :param count: Number of pages the rule governs, up to the next rule.
        :type count: int
        :returns: True when every page of the run can be numbered by one section of the same label.
        :rtype: bool
        """
        if self.style is LabelStyle.NONE or self.start < 1 or count < 1:
            return False
        try:
            self.style.write(self.start + count - 1)
        except ValueError:
            return False
        return True

    def label_at(self, index: int) -> str:
        """Write the label the rule gives the page at ``index``.

        :param index: Position of the page among the pages of the file, which is not before the rule's first page.
        :type index: int
        :returns: The prefix followed by the number in the style of the rule, or the prefix alone when the style writes
                  no number or cannot write this one, such as the number 0.
        :rtype: str
        """
        try:
            return f'{self.prefix}{self.style.write(self.start + index - self.first_index)}'
        except ValueError:
            return self.prefix

    def continues(self, index: int, *, style: LabelStyle, prefix: str, number: int) -> bool:
        """Tell whether the page at ``index`` with the given label parts is the next page of this rule's run.

        :param index: Position of the page among the pages of the file.
        :type index: int
        :param style: Style the page is numbered in.
        :type style: LabelStyle
        :param prefix: Prefix of the page's label.
        :type prefix: str
        :param number: Number the page takes, which a style that writes no number leaves at 1.
        :type number: int
        :returns: True when the page takes the number this run would give it, in the same style and prefix.
        :rtype: bool
        """
        if style is not self.style or prefix != self.prefix:
            return False
        return style is LabelStyle.NONE or number == self.start + index - self.first_index


@frozen(kw_only=True)
class PrintedNumber:
    """The number printed on one page of the book, as an EPUB page list names it.

    :ivar index: Position of the page among the pages of the output, from 0.
    :ivar label: The printed number, such as ``xii``, ``12`` or ``Plate I``.
    """

    index: int = field(validator=validators.ge(0))
    label: str = field(validator=validators.min_len(1))


@frozen(kw_only=True)
class BookLabeling:
    """The numbering of the pages of the output of a book, in the two forms its formats keep it.

    :ivar rules: Page label rules, which cover every page from the first, in the order of the pages.
    :ivar printed: The numbers printed on the pages, in the order of the pages, without a page that prints none.
    """

    rules: Sequence[PageLabelRule] = ()
    printed: Sequence[PrintedNumber] = ()
