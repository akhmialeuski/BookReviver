"""Tests for the changes in which processing jobs store what they computed, which read the book again first.

A job computes with no change open, so the book may change before it stores its answer. Each test makes such a change
at the moment the job has computed and not stored, and proves the job writes nothing over it.
"""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import JobState, Rendition, Stage, VersionState
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.events import PageVersionReady
from bookreviver.domain.ids import PageVersionId
from bookreviver.domain.keys import BookKeys
from bookreviver.domain.values import StageRun
from bookreviver.services.processing_jobs import NO_PAGE_PROCESSED
from bookreviver.services.stage_runs import RecipeRun
from bookreviver.services.steps import StepRunner
from tests.helpers.processing import change_versions, stored_job

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from bookreviver.domain.entities import Actor, BookRecord, Page, PageVersion
    from bookreviver.domain.enums import RunOutcome
    from bookreviver.domain.ids import BookId
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

CHANGE_ARGUMENT: str = 'change'
KEPT_ARGUMENT: str = 'kept'
UNTILED_VERSION_ID: PageVersionId = PageVersionId('8899aabbccddeeff')
STEP_FAILURE: str = 'The step failed after the page was deleted.'


async def untiled_version(kit: ProcessingKit) -> tuple[Actor, BookRecord, PageVersion]:
    """Seed a page with a ready version of the full scale whose tile pyramid is not cut.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The actor, the project and the version.
    :rtype: tuple[Actor, BookRecord, PageVersion]
    """
    actor, book_record = await kit.seed_record()
    page, _ = await kit.seed_scan_page(book_record)
    base = await kit.seed_base_version(book_record.id, page)
    untiled = evolve(base, id=UNTILED_VERSION_ID, tiles_ready=False)
    await kit.uow().change_book(book_record.id, lambda repos, _book_record: repos.page_versions.add(untiled))
    await kit.store_files(untiled, book_record.id)
    return actor, book_record, untiled


async def delete_the_version(kit: ProcessingKit, book_id: BookId, version: PageVersion) -> None:
    """Delete the version in a change of its own, as the removal of a page would.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_id: Project whose book holds the version.
    :type book_id: BookId
    :param version: The version to delete.
    :type version: PageVersion
    """
    await change_versions(kit, book_id, lambda versions: versions.delete(version.id))


async def fail_the_version(kit: ProcessingKit, book_id: BookId, version: PageVersion) -> None:
    """Mark the version failed in a change of its own, as the collection of old versions would.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_id: Project whose book holds the version.
    :type book_id: BookId
    :param version: The version to mark.
    :type version: PageVersion
    """
    await change_versions(kit, book_id, lambda versions: versions.update(evolve(version, state=VersionState.FAILED)))


async def cut_the_tiles_elsewhere(kit: ProcessingKit, book_id: BookId, version: PageVersion) -> None:
    """Mark the pyramid of the version cut in a change of its own, as the end of a run would.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_id: Project whose book holds the version.
    :type book_id: BookId
    :param version: The version to mark.
    :type version: PageVersion
    """
    await change_versions(kit, book_id, lambda versions: versions.update(evolve(version, tiles_ready=True)))


class TestCutTiles:
    """Tests for the ``cut-tiles`` job, which marks a version in a short change after it cut the pyramid."""

    @pytest.mark.parametrize(
        (CHANGE_ARGUMENT, KEPT_ARGUMENT),
        [
            pytest.param(delete_the_version, None, id='version-deleted'),
            pytest.param(fail_the_version, (VersionState.FAILED, False), id='version-changed'),
            pytest.param(cut_the_tiles_elsewhere, (VersionState.READY, True), id='tiles-cut-elsewhere'),
        ],
    )
    async def test_version_that_changed_while_its_pyramid_was_cut_is_not_marked(
        self,
        fx_kit: ProcessingKit,
        monkeypatch: pytest.MonkeyPatch,
        change: Callable[[ProcessingKit, BookId, PageVersion], Awaitable[None]],
        kept: tuple[VersionState, bool] | None,
    ) -> None:
        """Verify a version changed after its pyramid was cut keeps the change, and the viewer is not told it is cut.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param monkeypatch: Fixture putting the change between the cut and the change that marks the version.
        :type monkeypatch: pytest.MonkeyPatch
        :param change: What another request does to the version while its pyramid is cut.
        :type change: Callable[[ProcessingKit, BookId, PageVersion], Awaitable[None]]
        :param kept: The state of the version afterwards and whether it holds a cut pyramid, or None for a version that
                     is gone.
        :type kept: tuple[VersionState, bool] | None
        """
        _, book_record, untiled = await untiled_version(fx_kit)
        job = await fx_kit.parts(fx_kit.uow()).starter.enqueue_tiles(book_record.id, [untiled.id])
        assert job is not None
        cut = StepRunner.cut_tiles

        async def cut_then_change(self: StepRunner, keys: BookKeys, version: PageVersion) -> PageVersion:
            """Cut the pyramid, then let another request change the version before the job marks it.

            :param self: The runner that cuts.
            :type self: StepRunner
            :param keys: Keys of the project owning the page.
            :type keys: BookKeys
            :param version: The version to cut.
            :type version: PageVersion
            :returns: The version with its pyramid marked as cut.
            :rtype: PageVersion
            """
            cut_version = await cut(self, keys, version)
            await change(fx_kit, book_record.id, version)
            return cut_version

        monkeypatch.setattr(StepRunner, 'cut_tiles', cut_then_change)

        await fx_kit.work_queue()

        stored = await fx_kit.uow().read_book(book_record.id, lambda repos: repos.page_versions.find(untiled.id))
        expect(stored_job(fx_kit, job.id).state is JobState.SUCCEEDED)
        expect((stored and (stored.state, stored.tiles_ready)) == kept)
        expect(not any(isinstance(event, PageVersionReady) for event in fx_kit.events.published))
        if stored is None:
            tiles = BookKeys(book_record.id).version_rendition(untiled, Rendition.TILES)
            with pytest.raises(NotFoundError):
                async with fx_kit.assets.readable(tiles):
                    pass
        assert_expectations()


class TestRunPage:
    """Tests for the run of a stage over a page that is deleted while its step fails."""

    async def test_page_deleted_while_its_stage_failed_gets_no_record(
        self, fx_kit: ProcessingKit, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify the record of a failed stage is not written for a page that is gone, and the job ends for its reason.

        The step fails right after the page was deleted, so the change that records the failure finds no page. The page
        counts as failed, and the job fails with the reason of a run that processed no page.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param monkeypatch: Fixture making the run of the page delete it and fail.
        :type monkeypatch: pytest.MonkeyPatch
        """
        actor, book_record = await kit_with_page(fx_kit)

        async def delete_the_page_and_fail(
            _run: RecipeRun, page: Page, *_rest: object, **_options: object
        ) -> RunOutcome:
            """Delete the page in a change of its own, then fail as a step would.

            :param _run: The runner of the recipe, which the patched step replaces.
            :type _run: RecipeRun
            :param page: The page the stage is run on.
            :type page: Page
            :param _rest: The recipe, which the patched step ignores.
            :type _rest: object
            :param _options: The confirmation and the last step, which the patched step ignores.
            :type _options: object
            :returns: Never.
            :rtype: RunOutcome
            :raises ConflictError: Always.
            """
            await fx_kit.uow().change_book(book_record.id, lambda repos, _book_record: repos.pages.delete(page.id))
            raise ConflictError(STEP_FAILURE)

        monkeypatch.setattr(RecipeRun, 'run', delete_the_page_and_fail)
        job = await fx_kit.service().start_run(actor, book_record.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))

        await fx_kit.work_queue()

        stored = stored_job(fx_kit, job.id)
        assert (stored.state, stored.error) == (JobState.FAILED, NO_PAGE_PROCESSED)


async def kit_with_page(kit: ProcessingKit) -> tuple[Actor, BookRecord]:
    """Seed a project with one scan page whose base version is stored, the input of the geometry stage.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The actor and the project.
    :rtype: tuple[Actor, BookRecord]
    """
    actor, book_record = await kit.seed_record()
    page, _ = await kit.seed_scan_page(book_record)
    await kit.seed_base_version(book_record.id, page)
    return actor, book_record
