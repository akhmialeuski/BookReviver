"""Tests for the content of a book as one transaction sees it."""

from typing import TYPE_CHECKING

import pytest

from bookreviver.core.content import BookContent

if TYPE_CHECKING:
    from tests.harness.builders.processing import ProcessingKit

pytestmark = pytest.mark.anyio


class TestRecord:
    """Tests for BookContent.record."""

    async def test_the_record_is_the_one_of_the_book(self, fx_kit: ProcessingKit) -> None:
        """Verify the content gives the record the book was stored with.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, book_id = await fx_kit.seed_book()
        database = fx_kit.databases.open(book_id)
        stored = await database.read(lambda repos: repos.book_record.get())
        assert await database.read(lambda repos: BookContent(repos).record) == stored
