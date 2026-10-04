"""Tests for the detection of the content of the pages: the job, the request that queues it, and what it writes.

The processor that tells what an image shows is a fake that answers one content type for each page it reads, so the
tests tell which pages the job went over and what it did with the answers.
"""

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.changes import PageChanges
from bookreviver.domain.enums import (
    ContentSource,
    ContentType,
    JobKind,
    JobState,
    PageChange,
    PageKind,
    Stage,
    StageState,
)
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.events import PagesChanged, PageStageChanged
from bookreviver.domain.ids import PageId
from bookreviver.domain.values import ContentDetection, PageStageKey
from bookreviver.plugins.split_none import SplitNone
from tests.helpers.builders import make_job, make_page, make_page_stage
from tests.helpers.page_services import make_page_service
from tests.helpers.processing import ProcessingKit
from tests.helpers.processors import FakeContentProbe

if TYPE_CHECKING:
    from bookreviver.adapters.storage.local import LocalAssetStore
    from bookreviver.domain.entities import Actor, Job, Page, Project
    from bookreviver.services.pages import PageService

pytestmark = pytest.mark.anyio

ANSWERS: tuple[ContentType, ...] = (ContentType.TEXT, ContentType.COLOR_PICTURE, ContentType.BW_PICTURE)


def probe_kit(assets: LocalAssetStore, answers: list[ContentType | None]) -> tuple[ProcessingKit, FakeContentProbe]:
    """Build the processing kit over a catalogue with the fake processor that tells what an image shows.

    :param assets: Local asset store of the test.
    :type assets: LocalAssetStore
    :param answers: What the pages are found to show, in the order they are read.
    :type answers: list[ContentType | None]
    :returns: The kit and the fake processor.
    :rtype: tuple[ProcessingKit, FakeContentProbe]
    """
    probe = FakeContentProbe(answers)
    return ProcessingKit(assets, processors=[SplitNone(), probe]), probe


def pages_of(kit: ProcessingKit) -> PageService:
    """Build the page service over a new unit of work and the queue, the events and the clock of the kit.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The service.
    :rtype: PageService
    """
    return make_page_service(kit.uow(), kit.assets, (kit.events, kit.clock, kit.recording))


async def book_of(kit: ProcessingKit, kinds: list[PageKind]) -> tuple[Actor, Project, list[Page]]:
    """Seed a project with a page of each kind, every one with its scan and a ready base version.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param kinds: Kind of each page, in book order.
    :type kinds: list[PageKind]
    :returns: The actor, the project and its pages.
    :rtype: tuple[Actor, Project, list[Page]]
    """
    actor, project = await kit.seed_project()
    pages: list[Page] = []
    for index, kind in enumerate(kinds):
        page, _ = await kit.seed_scan_page(project, order_key=f'a{index}', kind=kind)
        await kit.seed_base_version(page)
        pages.append(page)
    return actor, project, pages


async def keep(kit: ProcessingKit, page: Page, content_type: ContentType, *, by_hand: bool = False) -> Page:
    """Write a content type into a stored page, as an earlier detection or the user would have.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param page: Page to change.
    :type page: Page
    :param content_type: What the page shows.
    :type content_type: ContentType
    :param by_hand: Whether the user set it.
    :type by_hand: bool
    :returns: The page as stored.
    :rtype: Page
    """
    uow = kit.uow()
    stored = await uow.pages.update(
        evolve(await uow.pages.get(page.id), content_type=content_type, content_by_hand=by_hand)
    )
    await uow.commit()
    return stored


async def detect(kit: ProcessingKit, actor: Actor, project: Project, page_ids: list[PageId] | None = None) -> Job:
    """Ask for the detection and let the worker do it.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param actor: Account owning the project.
    :type actor: Actor
    :param project: Project whose pages are detected.
    :type project: Project
    :param page_ids: The pages to detect, or None for those that are not detected yet.
    :type page_ids: list[PageId] | None
    :returns: The job as it stands when the worker is done.
    :rtype: Job
    """
    job = await pages_of(kit).start_detection(actor, project.id, page_ids)
    await kit.jobs().detect_content(job.id)
    return await kit.uow().jobs.get(job.id)


async def stored(kit: ProcessingKit, page: Page) -> Page:
    """Read a page as committed.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param page: The page.
    :type page: Page
    :returns: The page as the database holds it now.
    :rtype: Page
    """
    return await kit.uow().pages.get(page.id)


class TestDetectionJob:
    """Tests for the ``detect-content`` job."""

    async def test_what_the_processor_finds_is_written_into_each_page_and_announced(
        self, fx_asset_store: LocalAssetStore
    ) -> None:
        """Verify each page gets its own answer as a proposal, the job ends well, and one event names the pages.

        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        """
        kit, probe = probe_kit(fx_asset_store, list(ANSWERS))
        actor, project, pages = await book_of(kit, [PageKind.TEXT, PageKind.TEXT, PageKind.TITLE])

        job = await detect(kit, actor, project)

        found = [await stored(kit, page) for page in pages]
        expect((job.kind, job.state) == (JobKind.DETECT_CONTENT, JobState.SUCCEEDED))
        expect((job.progress.done, job.progress.total) == (len(pages), len(pages)))
        expect([page.content_type for page in found] == list(ANSWERS))
        expect(not any(page.content_by_hand for page in found))
        expect([page.content_source for page in found] == [ContentSource.DETECTED] * len(pages))
        expect(probe.reads == len(pages))
        events = [event for event in kit.events.published if isinstance(event, PagesChanged)]
        expect(
            [(set(event.page_ids), event.change) for event in events]
            == [({page.id for page in pages}, PageChange.EDITED)]
        )
        assert_expectations()

    async def test_a_job_that_names_no_pages_goes_over_the_pages_that_are_not_detected_and_not_a_role(
        self, fx_asset_store: LocalAssetStore
    ) -> None:
        """Verify a page that has a type, a page the user set, a cover, a blank page and a placeholder are left alone.

        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        """
        kit, probe = probe_kit(fx_asset_store, [ContentType.COLOR_PICTURE])
        kinds = [PageKind.TEXT, PageKind.TEXT, PageKind.TEXT, PageKind.COVER, PageKind.BLANK]
        actor, project, pages = await book_of(kit, kinds)
        found = await keep(kit, pages[1], ContentType.TEXT)
        by_hand = await keep(kit, pages[2], ContentType.BW_PICTURE, by_hand=True)
        uow = kit.uow()
        await uow.pages.add(make_page(project_id=project.id, order_key='a9'))
        await uow.commit()

        await detect(kit, actor, project)

        expect((await stored(kit, pages[0])).content_type is ContentType.COLOR_PICTURE)
        expect(await stored(kit, pages[1]) == found)
        expect(await stored(kit, pages[2]) == by_hand)
        expect([(await stored(kit, page)).content_type for page in pages[3:]] == [None, None])
        expect(probe.reads == 1)
        assert_expectations()

    async def test_a_job_that_names_pages_goes_over_those_whatever_they_have_and_gives_them_back_to_the_detection(
        self, fx_asset_store: LocalAssetStore
    ) -> None:
        """Verify a page the user set and a cover that are named are detected, and the first is no longer by hand.

        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        """
        kit, probe = probe_kit(fx_asset_store, [ContentType.TEXT, ContentType.BW_PICTURE])
        actor, project, pages = await book_of(kit, [PageKind.TEXT, PageKind.COVER, PageKind.TEXT])
        await keep(kit, pages[0], ContentType.COLOR_PICTURE, by_hand=True)

        job = await detect(kit, actor, project, [pages[0].id, pages[1].id])

        first, cover, other = [await stored(kit, page) for page in pages]
        expect(job.state is JobState.SUCCEEDED)
        expect((first.content_type, first.content_by_hand) == (ContentType.TEXT, False))
        expect((cover.content_type, cover.content_by_hand) == (ContentType.BW_PICTURE, False))
        expect(other.content_type is None)
        expect(probe.reads == 2)
        assert_expectations()

    async def test_a_page_that_cannot_be_read_fails_for_itself_and_the_job_goes_on(
        self, fx_asset_store: LocalAssetStore
    ) -> None:
        """Verify the page whose image the processor cannot read keeps no type, and the next page is detected.

        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        """
        kit, _ = probe_kit(fx_asset_store, [None, ContentType.BW_PICTURE])
        actor, project, pages = await book_of(kit, [PageKind.TEXT, PageKind.TEXT])

        job = await detect(kit, actor, project)

        expect(job.state is JobState.SUCCEEDED)
        expect([(await stored(kit, page)).content_type for page in pages] == [None, ContentType.BW_PICTURE])
        assert_expectations()

    async def test_a_job_that_reads_no_page_fails_with_a_reason(self, fx_asset_store: LocalAssetStore) -> None:
        """Verify a job whose every page failed ends failed, which the activity of the book shows.

        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        """
        kit, _ = probe_kit(fx_asset_store, [None, None])
        actor, project, _ = await book_of(kit, [PageKind.TEXT, PageKind.TEXT])

        job = await detect(kit, actor, project)

        expect(job.state is JobState.FAILED)
        expect('no page' in job.error)
        assert_expectations()

    async def test_a_machine_without_the_processor_leaves_the_pages_to_their_kind(
        self, fx_asset_store: LocalAssetStore
    ) -> None:
        """Verify a catalogue without the processor, as a machine without OpenCV has, gives a job that reads nothing.

        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        """
        kit = ProcessingKit(fx_asset_store, processors=[SplitNone()])
        actor, project, pages = await book_of(kit, [PageKind.TEXT])

        job = await detect(kit, actor, project)

        expect(job.state is JobState.SUCCEEDED)
        expect((await stored(kit, pages[0])).content_type is None)
        assert_expectations()

    async def test_a_page_without_a_ready_image_is_left_for_the_next_job(self, fx_asset_store: LocalAssetStore) -> None:
        """Verify a page whose base version is not made yet is not read and has no type, though the job ends well.

        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        """
        kit, probe = probe_kit(fx_asset_store, [ContentType.TEXT])
        actor, project, _ = await book_of(kit, [PageKind.TEXT])
        waiting, _ = await kit.seed_scan_page(project, order_key='a5')

        job = await detect(kit, actor, project)

        expect(job.state is JobState.SUCCEEDED)
        expect((await stored(kit, waiting)).content_type is None)
        expect(probe.reads == 1)
        assert_expectations()

    async def test_the_stages_of_a_page_whose_content_changed_for_the_steps_go_out_of_date(
        self, fx_asset_store: LocalAssetStore
    ) -> None:
        """Verify the Geometry stage of a page that turns out a picture is stale, and that of a text page is not.

        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        """
        kit, _ = probe_kit(fx_asset_store, [ContentType.BW_PICTURE, ContentType.TEXT])
        actor, project, pages = await book_of(kit, [PageKind.TEXT, PageKind.TEXT])
        uow = kit.uow()
        for page in pages:
            await uow.page_stages.save(make_page_stage(page_id=page.id, stage=Stage.GEOMETRY))
        await uow.commit()

        await detect(kit, actor, project)

        states = [(await kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))).state for page in pages]
        expect(states == [StageState.STALE, StageState.FRESH])
        changed = [event for event in kit.events.published if isinstance(event, PageStageChanged)]
        expect([event.stage.page_id for event in changed] == [pages[0].id])
        assert_expectations()

    async def test_a_job_that_was_cancelled_reads_nothing(self, fx_asset_store: LocalAssetStore) -> None:
        """Verify a job found cancelled when the worker takes it leaves the pages as they are.

        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        """
        kit, probe = probe_kit(fx_asset_store, [ContentType.TEXT])
        actor, project, pages = await book_of(kit, [PageKind.TEXT])
        job = await pages_of(kit).start_detection(actor, project.id, None)
        uow = kit.uow()
        await uow.jobs.update(evolve(job, state=JobState.CANCELLED))
        await uow.commit()

        await kit.jobs().detect_content(job.id)

        expect(probe.reads == 0)
        expect((await stored(kit, pages[0])).content_type is None)
        assert_expectations()


class TestStartDetection:
    """Tests for the requests that queue a detection."""

    async def test_the_request_queues_a_job_that_names_the_pages(self, fx_asset_store: LocalAssetStore) -> None:
        """Verify the job is stored with the pages as its parameters, announced, and handed to the queue.

        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        """
        kit, _ = probe_kit(fx_asset_store, [])
        actor, project, pages = await book_of(kit, [PageKind.TEXT, PageKind.TEXT])

        job = await pages_of(kit).start_detection(actor, project.id, [pages[1].id])

        expect((job.kind, job.state) == (JobKind.DETECT_CONTENT, JobState.QUEUED))
        expect(ContentDetection.from_map(job.params) == ContentDetection(page_ids=(pages[1].id,)))
        expect([queued.id for queued in kit.recording.enqueued] == [job.id])
        assert_expectations()

    async def test_a_page_that_is_not_in_the_book_is_refused(self, fx_asset_store: LocalAssetStore) -> None:
        """Verify naming a page that does not exist is an error, and no job is stored.

        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        """
        kit, _ = probe_kit(fx_asset_store, [])
        actor, project, _ = await book_of(kit, [PageKind.TEXT])

        with pytest.raises(NotFoundError):
            await pages_of(kit).start_detection(actor, project.id, [PageId(uuid4())])

        expect(not kit.recording.enqueued)
        assert_expectations()

    async def test_a_second_request_while_one_is_queued_is_refused(self, fx_asset_store: LocalAssetStore) -> None:
        """Verify one detection of the project at a time, so the pages of the second request are not silently dropped.

        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        """
        kit, _ = probe_kit(fx_asset_store, [])
        actor, project, pages = await book_of(kit, [PageKind.TEXT])
        await pages_of(kit).start_detection(actor, project.id, None)

        with pytest.raises(ConflictError, match='being detected'):
            await pages_of(kit).start_detection(actor, project.id, [pages[0].id])

    async def test_the_detection_of_new_pages_is_queued_once_while_one_waits(
        self, fx_asset_store: LocalAssetStore
    ) -> None:
        """Verify the worker that made images asks for one detection, whatever the number of jobs that ask.

        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        """
        kit, _ = probe_kit(fx_asset_store, [])
        _, project, _ = await book_of(kit, [PageKind.TEXT])

        await pages_of(kit).detect_new_pages(project.id)
        await pages_of(kit).detect_new_pages(project.id)

        [job] = kit.recording.enqueued
        expect(job.kind is JobKind.DETECT_CONTENT)
        expect(ContentDetection.from_map(job.params) == ContentDetection())
        assert_expectations()

    async def test_a_job_that_prepared_the_images_is_returned_for_the_worker_to_follow_up(
        self, fx_asset_store: LocalAssetStore
    ) -> None:
        """Verify the preparation of images returns the job it ran, and nothing for a job that was over already.

        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        """
        kit, _ = probe_kit(fx_asset_store, [])
        _, project, _ = await book_of(kit, [])
        uow = kit.uow()
        waiting = make_job(project_id=project.id, kind=JobKind.PREPARE_PAGES)
        over = make_job(project_id=project.id, kind=JobKind.PREPARE_PAGES, state=JobState.SUCCEEDED)
        await uow.jobs.add(waiting)
        await uow.jobs.add(over)
        await uow.commit()

        ran = await pages_of(kit).prepare_images(waiting.id)
        repeated = await pages_of(kit).prepare_images(over.id)

        expect(ran is not None and ran.project_id == project.id)
        expect(repeated is None)
        assert_expectations()


class TestChangeOfContent:
    """Tests for the user setting what a page shows."""

    async def test_the_content_the_user_sets_is_by_hand_and_marks_the_stages_stale(
        self, fx_asset_store: LocalAssetStore
    ) -> None:
        """Verify the type is kept as the user's, the page says so, and the stage of the page that read it is stale.

        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        """
        kit, _ = probe_kit(fx_asset_store, [])
        actor, project, pages = await book_of(kit, [PageKind.TEXT])
        uow = kit.uow()
        await uow.page_stages.save(make_page_stage(page_id=pages[0].id, stage=Stage.GEOMETRY))
        await uow.commit()

        overview = await pages_of(kit).update(
            actor, project.id, pages[0].id, PageChanges(content_type=ContentType.COLOR_PICTURE)
        )

        record = await kit.uow().page_stages.get(PageStageKey(pages[0].id, Stage.GEOMETRY))
        expect((overview.page.content_type, overview.page.content_by_hand) == (ContentType.COLOR_PICTURE, True))
        expect(overview.page.content_source is ContentSource.HAND)
        expect(record.state is StageState.STALE)
        assert_expectations()

    async def test_a_type_that_changes_nothing_for_the_steps_leaves_the_stages_up_to_date(
        self, fx_asset_store: LocalAssetStore
    ) -> None:
        """Verify choosing text for a page that was text does not make its stages stale.

        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        """
        kit, _ = probe_kit(fx_asset_store, [])
        actor, project, pages = await book_of(kit, [PageKind.TEXT])
        uow = kit.uow()
        await uow.page_stages.save(make_page_stage(page_id=pages[0].id, stage=Stage.GEOMETRY))
        await uow.commit()

        await pages_of(kit).update(actor, project.id, pages[0].id, PageChanges(content_type=ContentType.TEXT))

        assert (await kit.uow().page_stages.get(PageStageKey(pages[0].id, Stage.GEOMETRY))).state is StageState.FRESH
