"""Tests for marking the stage records stale when a processor that made their result has a new version."""

from datetime import timedelta
from typing import TYPE_CHECKING, Self

import pytest
from attrs import evolve

from bookreviver.domain.enums import Stage, StageState, VersionState
from bookreviver.domain.values import PageStageKey, ProcessorRef
from bookreviver.services.outdated_results import OutdatedResults
from tests.helpers.builders import EPOCH, make_page_stage, make_page_version
from tests.helpers.processors import CleanupProcessor, FakeProcessor

if TYPE_CHECKING:
    from datetime import datetime

    from bookreviver.domain.entities import Page, PageStage, PageVersion
    from bookreviver.domain.ids import PageVersionId
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
        await uow.page_versions.add(version)
        record = make_page_stage(page_id=self.page.id, stage=stage, head_version_id=version.id, state=state)
        await uow.page_stages.save(record)
        await uow.commit()
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
