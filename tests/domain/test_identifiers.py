"""Tests for the identifiers of a book: their schemes, their normalization and their value object."""

from typing import NamedTuple

import pytest

from bookreviver.domain.enums import IdentifierScheme
from bookreviver.domain.errors import DomainError, InvalidIdentifierError
from bookreviver.domain.values import BookIdentifier

ISBN_10: str = '0306406152'
ISBN_13: str = '9780306406157'
LONG_URL_TAIL: str = 'a' * 2_048


class Spelling(NamedTuple):
    """An identifier as written and the form the scheme normalizes it to."""

    scheme: IdentifierScheme
    raw: str
    normalized: str


class TestNormalize:
    """Tests for IdentifierScheme.normalize()."""

    @pytest.mark.parametrize(
        'spelling',
        [
            Spelling(IdentifierScheme.ISBN, ISBN_10, ISBN_10),
            Spelling(IdentifierScheme.ISBN, '0-306-40615-2', ISBN_10),
            Spelling(IdentifierScheme.ISBN, ' 0 306 40615 2 ', ISBN_10),
            Spelling(IdentifierScheme.ISBN, '978-0-306-40615-7', ISBN_13),
            Spelling(IdentifierScheme.ISBN, ISBN_13, ISBN_13),
            Spelling(IdentifierScheme.ISBN, '0-8044-2957-X', '080442957X'),
            Spelling(IdentifierScheme.ISBN, '0-8044-2957-x', '080442957X'),
            Spelling(IdentifierScheme.OCLC, ' 12345678 ', '12345678'),
            Spelling(IdentifierScheme.LCCN, 'n78-890351', 'n78890351'),
            Spelling(IdentifierScheme.LCCN, 'n 78890351 ', 'n78890351'),
            Spelling(IdentifierScheme.LCCN, '85-2', '85000002'),
            Spelling(IdentifierScheme.LCCN, '2001-000002', '2001000002'),
            Spelling(IdentifierScheme.LCCN, '75-425165//r75', '75425165'),
            Spelling(IdentifierScheme.LCCN, 'N78-890351', 'n78890351'),
            Spelling(IdentifierScheme.SHELFMARK, ' 18.123.4.56 ', '18.123.4.56'),
            Spelling(IdentifierScheme.URL, ' https://example.org/book/1 ', 'https://example.org/book/1'),
            Spelling(IdentifierScheme.URL, 'http://example.org', 'http://example.org'),
        ],
    )
    def test_gives_the_normalized_form_of_a_valid_identifier(self, spelling: Spelling) -> None:
        """Verify the same number written in different ways gives one normalized value.

        :param spelling: The scheme, the identifier as written and the form expected.
        :type spelling: Spelling
        """
        assert spelling.scheme.normalize(spelling.raw) == spelling.normalized

    @pytest.mark.parametrize(
        ('scheme', 'raw'),
        [
            (IdentifierScheme.ISBN, '0-306-40615-3'),
            (IdentifierScheme.ISBN, '978-0-306-40615-8'),
            (IdentifierScheme.ISBN, '0-8044-2957-0'),
            (IdentifierScheme.ISBN, '030640615'),
            (IdentifierScheme.ISBN, '03064X6152'),
            (IdentifierScheme.ISBN, '٠٣٠٦٤٠٦١٥٢'),
            (IdentifierScheme.ISBN, ''),
            (IdentifierScheme.OCLC, ''),
            (IdentifierScheme.OCLC, 'ocm1234'),
            (IdentifierScheme.OCLC, '12 34'),
            (IdentifierScheme.LCCN, '1234567'),
            (IdentifierScheme.LCCN, 'n78-89035x'),
            (IdentifierScheme.LCCN, '85-2-3'),
            (IdentifierScheme.LCCN, '/85000002'),
            (IdentifierScheme.SHELFMARK, '   '),
            (IdentifierScheme.SHELFMARK, 'x' * 301),
            (IdentifierScheme.URL, 'ftp://example.org/book'),
            (IdentifierScheme.URL, 'example.org/book'),
            (IdentifierScheme.URL, 'https://'),
            (IdentifierScheme.URL, 'https://exa mple.org'),
            (IdentifierScheme.URL, 'https://[::1'),
            (IdentifierScheme.URL, f'https://example.org/{LONG_URL_TAIL}'),
        ],
    )
    def test_rejects_an_identifier_that_breaks_the_rules_of_its_scheme(
        self, scheme: IdentifierScheme, raw: str
    ) -> None:
        """Verify a wrong check digit, a wrong length, wrong characters and a wrong address are refused.

        :param scheme: The scheme the identifier is written in.
        :type scheme: IdentifierScheme
        :param raw: The identifier that breaks the rules of the scheme.
        :type raw: str
        """
        with pytest.raises(InvalidIdentifierError, match=scheme.label):
            scheme.normalize(raw)

    def test_refusal_is_a_domain_error_and_a_value_error(self) -> None:
        """Verify the refusal is a value error, which Pydantic turns into a validation error, and a domain error."""
        assert issubclass(InvalidIdentifierError, DomainError)
        assert issubclass(InvalidIdentifierError, ValueError)


class TestBookIdentifier:
    """Tests for BookIdentifier."""

    def test_parse_normalizes_the_value(self) -> None:
        """Verify parsing builds an identifier holding the normalized value."""
        identifier = BookIdentifier.parse(IdentifierScheme.ISBN, '0-306-40615-2')
        assert (identifier.scheme, identifier.value) == (IdentifierScheme.ISBN, ISBN_10)

    def test_parse_rejects_an_invalid_value(self) -> None:
        """Verify parsing refuses a value with a wrong check digit."""
        with pytest.raises(InvalidIdentifierError):
            BookIdentifier.parse(IdentifierScheme.ISBN, '0-306-40615-3')

    def test_constructor_rejects_a_value_that_is_not_normalized(self) -> None:
        """Verify a value kept in the spelling of a person is refused, so stored values always compare equal."""
        with pytest.raises(InvalidIdentifierError, match='not normalized'):
            BookIdentifier(scheme=IdentifierScheme.ISBN, value='0-306-40615-2')

    def test_equal_numbers_written_differently_are_equal_identifiers(self) -> None:
        """Verify two spellings of one number give equal identifiers."""
        assert BookIdentifier.parse(IdentifierScheme.ISBN, '0-306-40615-2') == BookIdentifier.parse(
            IdentifierScheme.ISBN, ISBN_10
        )
