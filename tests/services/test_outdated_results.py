"""Tests for marking the stage records stale when a processor that made their result has a new version."""

from collections import Counter
from datetime import timedelta
from typing import TYPE_CHECKING, Self
from unittest.mock import AsyncMock

import pytest
from attrs import evolve

from bookreviver.domain.enums import Stage, StageState, VersionState
from bookreviver.domain.values import PageStageKey, ProcessorRef
from bookreviver.services.outdated_results import OutdatedResults
from tests.helpers.builders import EPOCH, make_page_stage, make_page_version
from tests.helpers.processors import CleanupProcessor, FakeProcessor

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from bookreviver.domain.entities import Page, PageStage, PageVersion
    from bookreviver.domain.ids import PageId, PageVersionId
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

# The version an older plugin made a result with, while the catalogue of the kit has the version 1 of its processors
OLD_VERSION: str = '0'
CURRENT_GEOMETRY: ProcessorRef = FakeProcessor.spec.ref
OUTDATED_GEOMETRY: ProcessorRef = evolve(CURRENT_GEOMETRY, version=OLD_VERSION)
CURRENT_CLEANUP: ProcessorRef = CleanupProcessor.spec.ref
# A processor whose plugin is not installed, which the catalogue of the kit knows nothing of
UNINSTALLED: ProcessorRef = ProcessorRef(key='geometry.removed', version='7')
# The time the clock of the kit shows when the records are checked, apart from the epoch the records are seeded at
CHECKED_AT: datetime = EPOCH + timedelta(hours=1)
# The stages that have a record once a page has its base version, a geometry result and a cleanup result
CHECKED_STAGES: tuple[Stage, ...] = (Stage.PAGE_SPLIT, Stage.GEOMETRY, Stage.CLEANUP)


class SeededPage:
    """A book of one page that has its base version, with the results the test commits on it.

    :ivar kit: What the processing services of the test share.
    :ivar page: The page.
    """

    def __init__(self, kit: ProcessingKit, page: Page) -> None:
        """Hold the kit and the page the results are committed on.

        :param kit: What the processing services of the test share.
        :type kit: ProcessingKit
        :param page: The page.
        :type page: Page
        """
        self.kit = kit
        self.page = page

    @classmethod
    async def seed(cls, kit: ProcessingKit) -> Self:
        """Commit a book of one page that has its base version, made by the current ``split.none``.

        :param kit: What the processing services of the test share.
        :type kit: ProcessingKit
        :returns: The seeded page.
        :rtype: Self
        """
        _, project = await kit.seed_project()
        page, _ = await kit.seed_scan_page(project, order_key='a0')
        await kit.seed_base_version(page)
        return cls(kit, page)

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
        uow = self.kit.uow()
        if input_id is None:
            input_id = (await uow.page_versions.list_for_page(self.page.id))[0].id
        version = evolve(
            make_page_version(page_id=self.page.id, minutes=stage.position + 1),
            stage=stage,
            processor=processor,
            input_id=input_id,
            state=VersionState.READY,
        )
        record = make_page_stage(page_id=self.page.id, stage=stage, head_version_id=version.id, state=state)
        async with uow.change_book(self.page.project_id):
            await uow.page_versions.add(version)
            await uow.page_stages.save(record)
        return version

    async def stored(self, stage: Stage) -> PageStage:
        """Read the committed record of a stage of the page.

        :param stage: The stage.
        :type stage: Stage
        :returns: The record.
        :rtype: PageStage
        """
        return await self.kit.uow().page_stages.get(PageStageKey(self.page.id, stage))


async def mark_stale(kit: ProcessingKit) -> list[PageStage]:
    """Check the records of every book at a later time than they were stored at.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The records that became stale.
    :rtype: list[PageStage]
    """
    kit.clock.moment = CHECKED_AT
    return await OutdatedResults(uow=kit.uow(), catalogue=kit.catalogue, clock=kit.clock).mark_stale()


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
    async def test_a_record_changed_between_the_check_and_the_block_is_left_as_the_request_wrote_it(
        self,
        fx_kit: ProcessingKit,
        monkeypatch: pytest.MonkeyPatch,
        meanwhile: Callable[[PageStage, PageVersionId], PageStage | None],
    ) -> None:
        """Verify the check marks nothing when its block finds the record not fresh, replaced or gone.

        The outdated record is found by reads outside any block, and the request changes it before the block of the
        book opens, which is what the re-read in the block is for.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param monkeypatch: Fixture that restores the patched repository after the test.
        :type monkeypatch: pytest.MonkeyPatch
        :param meanwhile: The record a request leaves after it was found outdated, or None when it deletes it.
        :type meanwhile: Callable[[PageStage, PageVersionId], PageStage | None]
        """
        page = await SeededPage.seed(fx_kit)
        await page.result(Stage.GEOMETRY, OUTDATED_GEOMETRY)
        record = await page.stored(Stage.GEOMETRY)
        base = (await fx_kit.uow().page_versions.list_for_page(page.page.id))[0]
        left = meanwhile(record, base.id)
        uow = fx_kit.uow()
        read = uow.pages.get
        changed = False

        async def get_then_change(page_id: PageId) -> Page:
            """Read the page of the record, then let a request change the record in a block of its own, once.

            :param page_id: Identifier of the page.
            :type page_id: PageId
            :returns: The page.
            :rtype: Page
            """
            nonlocal changed
            found = await read(page_id)
            if not changed:
                changed = True
                other = fx_kit.uow()
                async with other.change_book(found.project_id):
                    if left is None:
                        await other.page_stages.delete(record.key)
                    else:
                        await other.page_stages.save(left)
            return found

        monkeypatch.setattr(uow.pages, 'get', get_then_change)
        fx_kit.clock.moment = CHECKED_AT

        marked = await OutdatedResults(uow=uow, catalogue=fx_kit.catalogue, clock=fx_kit.clock).mark_stale()

        stored = await fx_kit.uow().page_stages.find(record.key)
        assert (marked, stored) == ([], left)

    async def test_fresh_stage_made_by_an_older_version_of_a_processor_becomes_stale(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the record is marked stale at the time of the check, keeps its version, and the base record is left.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        page = await SeededPage.seed(fx_kit)
        version = await page.result(Stage.GEOMETRY, OUTDATED_GEOMETRY)

        marked = await mark_stale(fx_kit)

        geometry = await page.stored(Stage.GEOMETRY)
        assert (marked, geometry.state, geometry.updated_at, geometry.head_version_id) == (
            [geometry],
            StageState.STALE,
            CHECKED_AT,
            version.id,
        )
        assert (await page.stored(Stage.PAGE_SPLIT)).state is StageState.FRESH

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
        uow = fx_kit.uow()
        reading = AsyncMock(wraps=uow.page_versions.list_by_ids)
        monkeypatch.setattr(uow.page_versions, 'list_by_ids', reading)
        fx_kit.clock.moment = CHECKED_AT

        await OutdatedResults(uow=uow, catalogue=fx_kit.catalogue, clock=fx_kit.clock).mark_stale()

        asked = Counter(version_id for call in reading.await_args_list for version_id in call.args[0])
        base = (await uow.page_versions.list_for_page(page.page.id))[0]
        assert asked == {base.id: 1, geometry.id: 1, cleanup.id: 1}
