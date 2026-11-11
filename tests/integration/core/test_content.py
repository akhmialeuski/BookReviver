"""Tests for the content of a book as one transaction sees it: its record, its pages and the versions of a page."""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.core.content import BookContent
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.stages import PageStageKey, Stage
from tests.harness.builders.stage_heads import seed_head

if TYPE_CHECKING:
    from collections.abc import Callable

    from bookreviver.domain.ids import BookId
    from bookreviver.domain.pages import Page
    from bookreviver.domain.versions import PageVersion
    from tests.harness.builders.processing import ProcessingKit

pytestmark = pytest.mark.anyio

FIRST_KEY: str = 'a0'
SECOND_KEY: str = 'a1'


async def seed_page(kit: ProcessingKit, book_id: BookId, order_key: str) -> tuple[Page, PageVersion]:
    """Seed a page with its ready base version.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_id: Identifier of the book owning the page.
    :type book_id: BookId
    :param order_key: Order key of the page.
    :type order_key: str
    :returns: The page and its base version.
    :rtype: tuple[Page, PageVersion]
    """
    page, _ = await kit.seed_scan_page(book_id, order_key=order_key)
    return page, await kit.seed_base_version(book_id, page)


async def in_book[T](kit: ProcessingKit, book_id: BookId, ask: Callable[[BookContent], T]) -> T:
    """Ask the content of a book in a transaction that only reads.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_id: Identifier of the book.
    :type book_id: BookId
    :param ask: What is asked of the content.
    :type ask: Callable[[BookContent], T]
    :returns: The answer.
    :rtype: T
    """
    return await kit.databases.open(book_id).read(lambda repos: ask(BookContent(repos)))


class TestBookContent:
    """Tests for BookContent and its pages."""

    async def test_the_record_is_the_one_of_the_book(self, fx_kit: ProcessingKit) -> None:
        """Verify the content gives the record the book was stored with.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, book_id = await fx_kit.seed_book()
        stored = await fx_kit.databases.open(book_id).read(lambda repos: repos.book_record.get())
        assert await in_book(fx_kit, book_id, lambda content: content.record) == stored

    async def test_the_pages_come_in_book_order(self, fx_kit: ProcessingKit) -> None:
        """Verify every page of the book is listed, in the order of the book.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, book_id = await fx_kit.seed_book()
        second, _ = await seed_page(fx_kit, book_id, SECOND_KEY)
        first, _ = await seed_page(fx_kit, book_id, FIRST_KEY)
        listed = await in_book(fx_kit, book_id, lambda content: [page.id for page in content.pages.all()])
        assert listed == [first.id, second.id]

    async def test_a_page_the_book_lacks_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Verify asking for a page of another book is refused.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, book_id = await fx_kit.seed_book()
        _, other_id = await fx_kit.seed_book()
        page, _ = await seed_page(fx_kit, other_id, FIRST_KEY)
        with pytest.raises(NotFoundError):
            await in_book(fx_kit, book_id, lambda content: content.pages.get(page.id))

    async def test_heads_are_the_current_versions_of_the_stage_but_those_left_out(self, fx_kit: ProcessingKit) -> None:
        """Verify the current versions of a stage are read for every page except the ones named.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, book_id = await fx_kit.seed_book()
        first, first_base = await seed_page(fx_kit, book_id, FIRST_KEY)
        second, second_base = await seed_page(fx_kit, book_id, SECOND_KEY)
        first_head = await seed_head(
            book_id, PageStageKey(first.id, Stage.GEOMETRY), after=first_base, databases=fx_kit.databases
        )
        second_head = await seed_head(
            book_id, PageStageKey(second.id, Stage.GEOMETRY), after=second_base, databases=fx_kit.databases
        )
        every = await in_book(fx_kit, book_id, lambda content: content.pages.heads(Stage.GEOMETRY))
        but_one = await in_book(
            fx_kit, book_id, lambda content: content.pages.heads(Stage.GEOMETRY, leaving_out=[first.id])
        )
        none = await in_book(fx_kit, book_id, lambda content: content.pages.heads(Stage.CLEANUP))
        expect({version.id for version in every} == {first_head.id, second_head.id})
        expect(but_one == [second_head])
        expect(none == [])
        assert_expectations()


class TestBookPage:
    """Tests for BookPage and the versions of a page."""

    async def test_the_stage_and_its_head_are_read_from_the_page(self, fx_kit: ProcessingKit) -> None:
        """Verify a page gives the record and the current version of a stage, and none for a stage that never ran.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, book_id = await fx_kit.seed_book()
        page, base = await seed_page(fx_kit, book_id, FIRST_KEY)
        key = PageStageKey(page.id, Stage.GEOMETRY)
        geometry = await seed_head(book_id, key, after=base, databases=fx_kit.databases)
        record, head, missing_record, missing_head = await in_book(
            fx_kit,
            book_id,
            lambda content: (
                content.pages.get(page.id).stage(Stage.GEOMETRY),
                content.pages.get(page.id).head(Stage.GEOMETRY),
                content.pages.get(page.id).stage(Stage.CLEANUP),
                content.pages.get(page.id).head(Stage.CLEANUP),
            ),
        )
        expect(record is not None and record.key == key)
        expect(head == geometry)
        expect(missing_record is None)
        expect(missing_head is None)
        assert_expectations()

    async def test_the_chain_follows_a_version_back_to_the_base(self, fx_kit: ProcessingKit) -> None:
        """Verify the chain of a version lists it and every version it was made from, the latest first.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, book_id = await fx_kit.seed_book()
        page, base = await seed_page(fx_kit, book_id, FIRST_KEY)
        geometry = await seed_head(
            book_id, PageStageKey(page.id, Stage.GEOMETRY), after=base, databases=fx_kit.databases
        )
        cleanup = await seed_head(
            book_id, PageStageKey(page.id, Stage.CLEANUP), after=geometry, databases=fx_kit.databases
        )
        chain, every = await in_book(
            fx_kit,
            book_id,
            lambda content: (
                content.pages.get(page.id).versions.chain(cleanup.id),
                content.pages.get(page.id).versions.all(),
            ),
        )
        expect(chain == [cleanup, geometry, base])
        expect({version.id for version in every} == {cleanup.id, geometry.id, base.id})
        assert_expectations()

    async def test_a_version_of_another_page_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Verify a page refuses the version of another page of the same book.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, book_id = await fx_kit.seed_book()
        page, _ = await seed_page(fx_kit, book_id, FIRST_KEY)
        _, other_base = await seed_page(fx_kit, book_id, SECOND_KEY)
        with pytest.raises(NotFoundError):
            await in_book(fx_kit, book_id, lambda content: content.pages.get(page.id).versions.get(other_base.id))
