"""Tests for marking the stage records stale when a processor that made their result has a new version."""

from collections import Counter
from datetime import timedelta
from typing import TYPE_CHECKING, Self

import pytest
from attrs import evolve

from bookreviver.adapters.persistence.sqlalchemy.book_database import SqlAlchemyBookDatabase
from bookreviver.adapters.persistence.sqlalchemy.repositories import SqlAlchemyPageVersionRepository
from bookreviver.domain.enums import Stage, StageState, VersionState
from bookreviver.domain.values import PageStageKey, ProcessorRef
from bookreviver.services.books import BooksService
from bookreviver.services.outdated_results import OutdatedResults
from tests.helpers.builders import EPOCH, make_page_stage, make_page_version
from tests.helpers.processors import CleanupProcessor, FakeProcessor

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Sequence

    from bookreviver.domain.entities import BookRecord, Page, PageStage, PageVersion
    from bookreviver.domain.ids import BookId, PageVersionId
    from bookreviver.ports.persistence import BookRepositories
    from tests.helpers.page_stage_writes import PageStageWrites
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

# The version an older plugin made a result with, while the catalogue of the kit has the version 1 of its processors
OLD_VERSION: str = '0'
CURRENT_GEOMETRY: ProcessorRef = FakeProcessor.spec.ref
OUTDATED_GEOMETRY: ProcessorRef = evolve(CURRENT_GEOMETRY, version=OLD_VERSION)
CURRENT_CLEANUP: ProcessorRef = CleanupProcessor.spec.ref
# A processor whose plugin is not installed, which the catalogue of the kit knows nothing of
UNINSTALLED: ProcessorRef = ProcessorRef(key='geometry.removed', version='7')
# The stages that have a record once a page has its base version, a geometry result and a cleanup result
CHECKED_STAGES: tuple[Stage, ...] = (Stage.PAGE_SPLIT, Stage.GEOMETRY, Stage.CLEANUP)


class SeededPage:
    """A book of one page that has its base version, with the results the test commits on it.

    :ivar kit: What the processing services of the test share.
    :ivar book_id: Identifier of the book.
    :ivar page: The page.
    """

    def __init__(self, kit: ProcessingKit, book_id: BookId, page: Page) -> None:
        """Hold the kit, the book and the page the results are committed on.

        :param kit: What the processing services of the test share.
        :type kit: ProcessingKit
        :param book_id: Identifier of the book.
        :type book_id: BookId
        :param page: The page.
        :type page: Page
        """
        self.kit = kit
        self.book_id = book_id
        self.page = page

    @classmethod
    async def seed(cls, kit: ProcessingKit) -> Self:
        """Commit a book of one page that has its base version, made by the current ``split.none``.

        :param kit: What the processing services of the test share.
        :type kit: ProcessingKit
        :returns: The seeded page.
        :rtype: Self
        """
        _, book_id = await kit.seed_book()
        page, _ = await kit.seed_scan_page(book_id, order_key='a0')
        await kit.seed_base_version(book_id, page)
        return cls(kit, book_id, page)

    async def result(
        self,
        stage: Stage,
        processor: ProcessorRef,
        *,
        input_id: PageVersionId | None = None,
        state: StageState = StageState.FRESH,
    ) -> PageVersion:
        """Commit a ready version a processor made, and the record of the stage that has it as its current version.

        :param stage: The stage of the version and of the record.
        :type stage: Stage
        :param processor: The processor, by key and version, that made the version.
        :type processor: ProcessorRef
        :param input_id: The version the step read, or None to read the base version of the page.
        :type input_id: PageVersionId | None
        :param state: State of the record.
        :type state: StageState
        :returns: The version.
        :rtype: PageVersion
        """
        if input_id is None:
            input_id = (await self.base()).id
        version = evolve(
            make_page_version(page_id=self.page.id, minutes=stage.position + 1),
            stage=stage,
            processor=processor,
            input_id=input_id,
            state=VersionState.READY,
        )
        record = make_page_stage(page_id=self.page.id, stage=stage, head_version_id=version.id, state=state)

        def add_result(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Add the version and the record of the stage that has it as its current version.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            repos.page_versions.add(version)
            repos.page_stages.save(record)

        await self.kit.databases.open(self.book_id).change(add_result)
        return version

    async def base(self) -> PageVersion:
        """Read the base version of the page, which is the earliest version it has.

        :returns: The base version.
        :rtype: PageVersion
        """
        return (
            await self.kit.databases.open(self.book_id).read(
                lambda repos: repos.page_versions.list_for_page(self.page.id)
            )
        )[0]

    async def stored(self, stage: Stage) -> PageStage:
        """Read the committed record of a stage of the page.

        :param stage: The stage.
        :type stage: Stage
        :returns: The record.
        :rtype: PageStage
        """
        return await self.kit.databases.open(self.book_id).read(
            lambda repos: repos.page_stages.get(PageStageKey(self.page.id, stage))
        )


async def mark_stale(kit: ProcessingKit) -> list[PageStage]:
    """Check the records of every book.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The records that became stale.
    :rtype: list[PageStage]
    """
    catalogue = kit.catalogue()
    return await OutdatedResults(
        books=BooksService(catalogue=catalogue),
        processors=kit.processors,
    ).mark_stale()


class TestMarkStale:
    """Tests for ``OutdatedResults.mark_stale``."""

    @pytest.mark.parametrize(
        'meanwhile',
        [
            pytest.param(
                lambda record, _: evolve(record, state=StageState.STALE, updated_at=EPOCH + timedelta(minutes=30)),
                id='marked-stale',
            ),
            pytest.param(lambda record, base_id: evolve(record, head_version_id=base_id), id='run-again'),
            pytest.param(lambda _record, _base_id: None, id='deleted'),
        ],
    )
    async def test_a_record_changed_between_the_check_and_the_change_is_left_as_the_request_wrote_it(
        self,
        fx_kit: ProcessingKit,
        monkeypatch: pytest.MonkeyPatch,
        meanwhile: Callable[[PageStage, PageVersionId], PageStage | None],
    ) -> None:
        """Verify the check marks nothing when its change finds the record not fresh, replaced or gone.

        The outdated record is found by a transaction that only reads, and the request changes it before the change of
        the book opens, which is what the re-read in that change is for.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param monkeypatch: Fixture that restores the patched catalogue database after the test.
        :type monkeypatch: pytest.MonkeyPatch
        :param meanwhile: The record a request leaves after it was found outdated, or None when it deletes it.
        :type meanwhile: Callable[[PageStage, PageVersionId], PageStage | None]
        """
        page = await SeededPage.seed(fx_kit)
        await page.result(Stage.GEOMETRY, OUTDATED_GEOMETRY)
        record = await page.stored(Stage.GEOMETRY)
        left = meanwhile(record, (await page.base()).id)
        catalogue = fx_kit.catalogue()
        read = SqlAlchemyBookDatabase.read
        changed = False

        def request_changes_record(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Write the record as a request that came after the check found it outdated leaves it.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            if left is None:
                repos.page_stages.delete(record.key)
            else:
                repos.page_stages.save(left)

        async def read_then_change[T](self: SqlAlchemyBookDatabase, work: Callable[[BookRepositories], T]) -> T:
            """Read as the book database does, then let a request change the record in a change of its own, once.

            :param self: The book database that is read.
            :type self: SqlAlchemyBookDatabase
            :param work: Function over the repositories of the transaction.
            :type work: Callable[[BookRepositories], T]
            :returns: What the work returns.
            :rtype: T
            """
            nonlocal changed
            found = await read(self, work)
            if not changed:
                changed = True
                await fx_kit.databases.open(page.book_id).change(request_changes_record)
            return found

        monkeypatch.setattr(SqlAlchemyBookDatabase, 'read', read_then_change)
        marked = await OutdatedResults(
            books=BooksService(catalogue=catalogue),
            processors=fx_kit.processors,
        ).mark_stale()

        stored = await fx_kit.databases.open(page.book_id).read(lambda repos: repos.page_stages.find(record.key))
        assert (marked, stored) == ([], left)

    async def test_stage_built_on_an_outdated_version_of_an_earlier_stage_becomes_stale(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a record of the cleanup stage is marked through the outdated geometry version it read.

        The geometry record is stale already, so only the walk back from the cleanup version through the stages can
        find the outdated version.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        page = await SeededPage.seed(fx_kit)
        geometry = await page.result(Stage.GEOMETRY, OUTDATED_GEOMETRY, state=StageState.STALE)
        await page.result(Stage.CLEANUP, CURRENT_CLEANUP, input_id=geometry.id)

        marked = await mark_stale(fx_kit)

        assert [record.stage for record in marked] == [Stage.CLEANUP]
        assert (await page.stored(Stage.CLEANUP)).state is StageState.STALE

    async def test_stage_whose_whole_chain_uses_the_installed_versions_stays_fresh(self, fx_kit: ProcessingKit) -> None:
        """Verify nothing is marked or committed when every version in the chains was made by an installed version.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        page = await SeededPage.seed(fx_kit)
        geometry = await page.result(Stage.GEOMETRY, CURRENT_GEOMETRY)
        await page.result(Stage.CLEANUP, CURRENT_CLEANUP, input_id=geometry.id)
        before = [await page.stored(stage) for stage in CHECKED_STAGES]

        marked = await mark_stale(fx_kit)

        after = [await page.stored(stage) for stage in CHECKED_STAGES]
        assert (marked, after) == ([], before)

    @pytest.mark.parametrize('state', [StageState.STALE, StageState.FAILED])
    async def test_stale_and_failed_records_are_left_as_they_are(
        self, fx_kit: ProcessingKit, state: StageState
    ) -> None:
        """Verify a record that is stale or failed already keeps its state and its time, whatever its chain holds.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param state: State the record has before the check.
        :type state: StageState
        """
        page = await SeededPage.seed(fx_kit)
        await page.result(Stage.GEOMETRY, OUTDATED_GEOMETRY, state=state)
        before = await page.stored(Stage.GEOMETRY)

        marked = await mark_stale(fx_kit)

        assert (marked, await page.stored(Stage.GEOMETRY)) == ([], before)

    async def test_processor_missing_from_the_catalogue_is_not_outdated(self, fx_kit: ProcessingKit) -> None:
        """Verify a version made by a processor that is not installed says nothing about its version, so it stays.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        page = await SeededPage.seed(fx_kit)
        await page.result(Stage.GEOMETRY, UNINSTALLED)

        marked = await mark_stale(fx_kit)

        assert (marked, (await page.stored(Stage.GEOMETRY)).state) == ([], StageState.FRESH)

    async def test_the_records_of_every_book_are_checked_and_only_the_outdated_ones_are_marked(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the records of two books are read together, and the book whose results are current keeps its own.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        outdated_page = await SeededPage.seed(fx_kit)
        current_page = await SeededPage.seed(fx_kit)
        await outdated_page.result(Stage.GEOMETRY, OUTDATED_GEOMETRY)
        await current_page.result(Stage.GEOMETRY, CURRENT_GEOMETRY)

        marked = await mark_stale(fx_kit)

        assert (
            [record.page_id for record in marked],
            (await outdated_page.stored(Stage.GEOMETRY)).state,
            (await current_page.stored(Stage.GEOMETRY)).state,
        ) == ([outdated_page.page.id], StageState.STALE, StageState.FRESH)

    async def test_a_version_that_several_records_lead_to_is_read_once(
        self, fx_kit: ProcessingKit, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify the base version and the geometry version, which the later stages of one page read, are read once.

        The cleanup record leads to the geometry version and through it to the base version, and the geometry record
        leads to both as well, so a walk of each record on its own reads them again and again.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param monkeypatch: Replaces the read of the versions by one that counts what it is asked for.
        :type monkeypatch: pytest.MonkeyPatch
        """
        page = await SeededPage.seed(fx_kit)
        geometry = await page.result(Stage.GEOMETRY, CURRENT_GEOMETRY)
        cleanup = await page.result(Stage.CLEANUP, CURRENT_CLEANUP, input_id=geometry.id)
        read = SqlAlchemyPageVersionRepository.list_by_ids
        asked: Counter[PageVersionId] = Counter()

        def counting_list_by_ids(
            repository: SqlAlchemyPageVersionRepository, version_ids: Collection[PageVersionId]
        ) -> Sequence[PageVersion]:
            """Count every identifier the repository is asked for, then read as it does.

            :param repository: The repository of the transaction that reads.
            :type repository: SqlAlchemyPageVersionRepository
            :param version_ids: Identifiers of the versions to read.
            :type version_ids: Collection[PageVersionId]
            :returns: The versions found.
            :rtype: Sequence[PageVersion]
            """
            asked.update(version_ids)
            return read(repository, version_ids)

        monkeypatch.setattr(SqlAlchemyPageVersionRepository, 'list_by_ids', counting_list_by_ids)

        catalogue = fx_kit.catalogue()
        await OutdatedResults(
            books=BooksService(catalogue=catalogue),
            processors=fx_kit.processors,
        ).mark_stale()

        assert asked == {(await page.base()).id: 1, geometry.id: 1, cleanup.id: 1}

    async def test_the_outdated_records_of_a_book_are_written_by_one_save_many_and_never_by_save(
        self, fx_kit: ProcessingKit, fx_page_stage_writes: PageStageWrites
    ) -> None:
        """Verify the two outdated records of a book are written by one ``save_many`` holding both, never by ``save``.

        The end state of the records is the same when each one is written on its own, so only a record of the calls
        tells a batch of one statement from a statement for each record.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_page_stage_writes: Record of the writes of the page stage repository, not yet started.
        :type fx_page_stage_writes: PageStageWrites
        """
        page = await SeededPage.seed(fx_kit)
        geometry = await page.result(Stage.GEOMETRY, OUTDATED_GEOMETRY)
        await page.result(Stage.CLEANUP, CURRENT_CLEANUP, input_id=geometry.id)
        fx_page_stage_writes.start()

        marked = await mark_stale(fx_kit)

        assert (len(marked), fx_page_stage_writes.batches, fx_page_stage_writes.saves) == (2, [marked], [])
