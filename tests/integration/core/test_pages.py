"""Tests for the pages of a book and the versions of a page, and for the rule of what a stage reads on a page."""

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.core.content import BookContent
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.images import Renditions
from bookreviver.domain.stages import PageStageKey, Stage, StageState
from bookreviver.domain.versions import ProcessorRef, VersionState
from tests.harness.builders.domain import make_page_stage, make_page_version
from tests.harness.builders.stage_heads import seed_head

if TYPE_CHECKING:
    from collections.abc import Callable, Collection

    from bookreviver.domain.ids import BookId, PageId
    from bookreviver.domain.pages import Page
    from bookreviver.domain.versions import PageVersion
    from bookreviver.ports.persistence import BookRepositories
    from tests.harness.adapters.database import LocatedBookDatabases
    from tests.harness.builders.processing import ProcessingKit

pytestmark = pytest.mark.anyio

WINDOW_SIZE: int = 5
FIRST_KEY: str = 'a0'
SECOND_KEY: str = 'a1'
THIRD_KEY: str = 'a2'
WANTED_KEY: str = 'geometry.wanted'


async def seed_page(kit: ProcessingKit, book_id: BookId, order_key: str) -> tuple[Page, PageVersion]:
    """Seed a page with its ready base version, which the record of the page split names as current.

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


async def seed_loose_base(
    book_id: BookId, page: Page, *, minutes: int, pending: bool = False, databases: LocatedBookDatabases
) -> PageVersion:
    """Commit a base version of a page that no record of a stage names, as a page cut from its scan again has.

    :param book_id: Identifier of the book.
    :type book_id: BookId
    :param page: The page.
    :type page: Page
    :param minutes: Minutes after the epoch the version was created, which orders the base versions of the page.
    :type minutes: int
    :param pending: Whether the version is still to make, with no files published, where a ready one has them.
    :type pending: bool
    :param databases: Opens the databases of the books of the test.
    :type databases: LocatedBookDatabases
    :returns: The version.
    :rtype: PageVersion
    """
    version = evolve(
        make_page_version(page_id=page.id, minutes=minutes),
        state=VersionState.PENDING if pending else VersionState.READY,
        renditions=Renditions(ready=not pending),
    )
    await databases.open(book_id).change(lambda repos, _book_record: repos.page_versions.add(version))
    return version


async def inputs_of(
    book_id: BookId, page_ids: Collection[PageId], stage: Stage, *, databases: LocatedBookDatabases
) -> dict[PageId, PageVersion]:
    """Find the version a stage reads on pages, in a transaction that only reads.

    :param book_id: Book that holds the pages.
    :type book_id: BookId
    :param page_ids: The pages.
    :type page_ids: Collection[PageId]
    :param stage: The stage that reads.
    :type stage: Stage
    :param databases: Opens the databases of the books of the test.
    :type databases: LocatedBookDatabases
    :returns: The version read, by page.
    :rtype: dict[PageId, PageVersion]
    """
    return await databases.open(book_id).read(lambda repos: BookContent(repos).pages.inputs(stage, page_ids))


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


class TestPageVersionsAll:
    """Tests for PageVersions.all."""

    async def test_every_version_of_the_page_is_listed(self, fx_kit: ProcessingKit) -> None:
        """Verify the versions of a page are its base version and every version made from it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, book_id = await fx_kit.seed_book()
        page, base = await seed_page(fx_kit, book_id, FIRST_KEY)
        geometry = await seed_head(
            book_id, PageStageKey(page.id, Stage.GEOMETRY), after=base, databases=fx_kit.databases
        )
        every = await in_book(fx_kit, book_id, lambda content: content.pages.get(page.id).versions.all())
        assert {version.id for version in every} == {geometry.id, base.id}


class TestPageVersionsGet:
    """Tests for PageVersions.get."""

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


class TestPageVersionsChain:
    """Tests for PageVersions.chain."""

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
        chain = await in_book(fx_kit, book_id, lambda content: content.pages.get(page.id).versions.chain(cleanup.id))
        assert chain == [cleanup, geometry, base]


class TestBookPageStage:
    """Tests for BookPage.stage."""

    async def test_the_record_of_a_stage_is_read_and_none_for_a_stage_that_never_ran(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a page gives the record of a stage it went through, and None for a stage that never ran on it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, book_id = await fx_kit.seed_book()
        page, base = await seed_page(fx_kit, book_id, FIRST_KEY)
        key = PageStageKey(page.id, Stage.GEOMETRY)
        await seed_head(book_id, key, after=base, databases=fx_kit.databases)
        record, missing = await in_book(
            fx_kit,
            book_id,
            lambda content: (
                content.pages.get(page.id).stage(Stage.GEOMETRY),
                content.pages.get(page.id).stage(Stage.CLEANUP),
            ),
        )
        expect(record is not None and record.key == key)
        expect(missing is None)
        assert_expectations()


class TestBookPageHead:
    """Tests for BookPage.head."""

    async def test_the_current_version_is_read_and_none_for_a_stage_that_never_ran(self, fx_kit: ProcessingKit) -> None:
        """Verify a page gives the current version of a stage it went through, and None for a stage that never ran.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, book_id = await fx_kit.seed_book()
        page, base = await seed_page(fx_kit, book_id, FIRST_KEY)
        geometry = await seed_head(
            book_id, PageStageKey(page.id, Stage.GEOMETRY), after=base, databases=fx_kit.databases
        )
        head, missing = await in_book(
            fx_kit,
            book_id,
            lambda content: (
                content.pages.get(page.id).head(Stage.GEOMETRY),
                content.pages.get(page.id).head(Stage.CLEANUP),
            ),
        )
        expect(head == geometry)
        expect(missing is None)
        assert_expectations()


class TestBookPagesAll:
    """Tests for BookPages.all."""

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


class TestBookPagesGet:
    """Tests for BookPages.get."""

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


class TestBookPagesHeads:
    """Tests for BookPages.heads."""

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


class TestBookPagesMadeBy:
    """Tests for BookPages.made_by."""

    async def test_the_version_of_a_processor_is_found_behind_the_current_one(self, fx_kit: ProcessingKit) -> None:
        """Verify the version a processor made is found by following the current version back through its stage.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, book_id = await fx_kit.seed_book()
        page, base = await seed_page(fx_kit, book_id, FIRST_KEY)
        other, other_base = await seed_page(fx_kit, book_id, SECOND_KEY)
        key = PageStageKey(page.id, Stage.GEOMETRY)
        early = await seed_head(book_id, key, after=base, databases=fx_kit.databases)
        marked = evolve(early, processor=ProcessorRef(key=WANTED_KEY, version='1'))
        await fx_kit.databases.open(book_id).change(lambda repos, _record: repos.page_versions.update(marked))
        await seed_head(book_id, key, after=marked, databases=fx_kit.databases)
        await seed_head(book_id, PageStageKey(other.id, Stage.GEOMETRY), after=other_base, databases=fx_kit.databases)
        found = await in_book(fx_kit, book_id, lambda content: content.pages.made_by(Stage.GEOMETRY, WANTED_KEY))
        left_out = await in_book(
            fx_kit,
            book_id,
            lambda content: content.pages.made_by(Stage.GEOMETRY, WANTED_KEY, leaving_out=[page.id]),
        )
        expect([version.id for version in found] == [marked.id])
        expect(left_out == [])
        assert_expectations()


class TestBookPagesInputs:
    """Tests for BookPages.inputs."""

    async def test_the_nearest_earlier_stage_with_a_head_is_read(self, fx_kit: ProcessingKit) -> None:
        """Verify each stage reads the current version of the stage right before it that has one.

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
        expect(await inputs_of(book_id, [page.id], Stage.GEOMETRY, databases=fx_kit.databases) == {page.id: base})
        expect(await inputs_of(book_id, [page.id], Stage.CLEANUP, databases=fx_kit.databases) == {page.id: geometry})
        expect(await inputs_of(book_id, [page.id], Stage.LAYOUT, databases=fx_kit.databases) == {page.id: cleanup})
        assert_expectations()

    async def test_an_earlier_stage_without_a_head_is_skipped_for_the_one_before_it(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a stage that never ran, or has a record with no current version, falls through to the next one back.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, book_id = await fx_kit.seed_book()
        page, base = await seed_page(fx_kit, book_id, FIRST_KEY)
        geometry = await seed_head(
            book_id, PageStageKey(page.id, Stage.GEOMETRY), after=base, databases=fx_kit.databases
        )
        empty = make_page_stage(page_id=page.id, stage=Stage.CLEANUP, head_version_id=None)
        await fx_kit.databases.open(book_id).change(lambda repos, _book_record: repos.page_stages.save(empty))
        assert await inputs_of(book_id, [page.id], Stage.LAYOUT, databases=fx_kit.databases) == {page.id: geometry}

    async def test_a_head_whose_files_are_not_ready_is_skipped_like_a_missing_one(self, fx_kit: ProcessingKit) -> None:
        """Verify a stage reads the version before a current version that has no published image.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, book_id = await fx_kit.seed_book()
        page, base = await seed_page(fx_kit, book_id, FIRST_KEY)
        geometry = await seed_head(
            book_id, PageStageKey(page.id, Stage.GEOMETRY), after=base, databases=fx_kit.databases
        )
        await seed_head(
            book_id,
            PageStageKey(page.id, Stage.CLEANUP),
            after=geometry,
            ready=False,
            databases=fx_kit.databases,
        )
        assert await inputs_of(book_id, [page.id], Stage.LAYOUT, databases=fx_kit.databases) == {page.id: geometry}

    async def test_the_latest_ready_base_version_is_read_when_no_earlier_stage_has_a_head(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a page cut from its scan again is read from its newest base version that is ready.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, book_id = await fx_kit.seed_book()
        page, _ = await fx_kit.seed_scan_page(book_id, order_key=FIRST_KEY)
        await seed_loose_base(book_id, page, minutes=1, databases=fx_kit.databases)
        latest = await seed_loose_base(book_id, page, minutes=2, databases=fx_kit.databases)
        await seed_loose_base(book_id, page, minutes=3, pending=True, databases=fx_kit.databases)
        assert await inputs_of(book_id, [page.id], Stage.GEOMETRY, databases=fx_kit.databases) == {page.id: latest}

    async def test_a_page_with_no_image_to_read_has_no_entry(self, fx_kit: ProcessingKit) -> None:
        """Verify a page that has no version, and a base version that is not ready, are left out of the answer.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, book_id = await fx_kit.seed_book()
        empty, _ = await fx_kit.seed_scan_page(book_id, order_key=FIRST_KEY)
        unready, _ = await fx_kit.seed_scan_page(book_id, order_key=SECOND_KEY)
        await seed_loose_base(book_id, unready, minutes=1, pending=True, databases=fx_kit.databases)
        assert await inputs_of(book_id, [empty.id, unready.id], Stage.GEOMETRY, databases=fx_kit.databases) == {}

    async def test_the_page_split_reads_the_scan_so_it_has_no_input_version(self, fx_kit: ProcessingKit) -> None:
        """Verify the page split gets nothing, though the page has a base version a later stage would read.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, book_id = await fx_kit.seed_book()
        page, _ = await seed_page(fx_kit, book_id, FIRST_KEY)
        assert await inputs_of(book_id, [page.id], Stage.PAGE_SPLIT, databases=fx_kit.databases) == {}

    async def test_a_window_of_pages_is_answered_in_one_call_page_by_page(self, fx_kit: ProcessingKit) -> None:
        """Verify every page of the window gets what it reads, from its own stages and not from a neighbour's.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, book_id = await fx_kit.seed_book()
        with_head, with_head_base = await seed_page(fx_kit, book_id, FIRST_KEY)
        geometry = await seed_head(
            book_id, PageStageKey(with_head.id, Stage.GEOMETRY), after=with_head_base, databases=fx_kit.databases
        )
        base_only, base = await seed_page(fx_kit, book_id, SECOND_KEY)
        empty, _ = await fx_kit.seed_scan_page(book_id, order_key=THIRD_KEY)
        found = await inputs_of(
            book_id, [with_head.id, base_only.id, empty.id], Stage.CLEANUP, databases=fx_kit.databases
        )
        assert found == {with_head.id: geometry, base_only.id: base}

    async def test_a_window_is_read_with_the_same_queries_whatever_its_size(self, fx_kit: ProcessingKit) -> None:
        """Verify the records, the current versions and the base versions are each read once for the whole window.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, book_id = await fx_kit.seed_book()
        pages = []
        for number in range(WINDOW_SIZE):
            if number >= WINDOW_SIZE - 2:
                # The last pages have a base version no stage names, so the base versions have to be read
                page, _ = await fx_kit.seed_scan_page(book_id, order_key=f'a{number}')
                await seed_loose_base(book_id, page, minutes=number, databases=fx_kit.databases)
            else:
                page, base = await seed_page(fx_kit, book_id, f'a{number}')
                if number % 2 == 0:
                    await seed_head(
                        book_id, PageStageKey(page.id, Stage.GEOMETRY), after=base, databases=fx_kit.databases
                    )
            pages.append(page)

        def work(repos: BookRepositories) -> tuple[dict[PageId, PageVersion], tuple[int, int, int]]:
            """Find the inputs of the window and count the reads of the records, the versions and the base versions.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: The versions read by page, and the number of the three reads.
            :rtype: tuple[dict[PageId, PageVersion], tuple[int, int, int]]
            """
            with (
                patch.object(repos.page_stages, 'list_for_pages', wraps=repos.page_stages.list_for_pages) as records,
                patch.object(repos.page_versions, 'list_by_ids', wraps=repos.page_versions.list_by_ids) as versions,
                patch.object(
                    repos.page_versions, 'list_base_versions', wraps=repos.page_versions.list_base_versions
                ) as bases,
            ):
                found = BookContent(repos).pages.inputs(Stage.CLEANUP, [page.id for page in pages])
            return found, (records.call_count, versions.call_count, bases.call_count)

        found, reads = await fx_kit.databases.open(book_id).read(work)
        expect(len(found) == WINDOW_SIZE)
        expect(reads == (1, 1, 1))
        assert_expectations()

    async def test_a_record_of_a_later_stage_is_never_read(self, fx_kit: ProcessingKit) -> None:
        """Verify a stage does not read the current version of a stage after it, whatever the page holds there.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, book_id = await fx_kit.seed_book()
        page, base = await seed_page(fx_kit, book_id, FIRST_KEY)
        geometry = await seed_head(
            book_id, PageStageKey(page.id, Stage.GEOMETRY), after=base, databases=fx_kit.databases
        )
        await seed_head(book_id, PageStageKey(page.id, Stage.CLEANUP), after=geometry, databases=fx_kit.databases)
        later = make_page_stage(
            page_id=page.id, stage=Stage.LAYOUT, head_version_id=geometry.id, state=StageState.STALE
        )
        await fx_kit.databases.open(book_id).change(lambda repos, _book_record: repos.page_stages.save(later))
        assert await inputs_of(book_id, [page.id], Stage.GEOMETRY, databases=fx_kit.databases) == {page.id: base}
