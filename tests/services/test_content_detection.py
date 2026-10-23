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
    PageKind,
    Stage,
    StageState,
)
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.ids import PageId
from bookreviver.domain.values import ContentDetection, PageStageKey
from bookreviver.plugins.split_none import SplitNone
from bookreviver.services.content_detection import ContentDetector
from tests.helpers.builders import make_page, make_page_stage
from tests.helpers.page_services import make_page_service
from tests.helpers.processing import ProcessingKit, stored_job
from tests.helpers.processors import FakeContentProbe

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.adapters.storage.local import LocalAssetStore
    from bookreviver.domain.entities import Actor, BookRecord, Job, Page, PageVersion
    from bookreviver.domain.ids import BookId
    from bookreviver.ports.persistence import BookRepositories
    from bookreviver.services.pages import PageService
    from tests.helpers.database import UnitOfWorkFactory

pytestmark = pytest.mark.anyio

type ProbeKitFactory = Callable[[list[ContentType | None]], tuple[ProcessingKit, FakeContentProbe]]

ANSWERS: tuple[ContentType, ...] = (ContentType.TEXT, ContentType.COLOR_PICTURE, ContentType.BW_PICTURE)


@pytest.fixture
def fx_probe_kit(
    fx_database: SqlDatabase, fx_uow: UnitOfWorkFactory, fx_asset_store: LocalAssetStore
) -> ProbeKitFactory:
    """Return a function building the processing kit with the fake processor that tells what an image shows.

    :param fx_database: Database of the test.
    :type fx_database: SqlDatabase
    :param fx_uow: Function opening a new unit of work over the database of the test.
    :type fx_uow: UnitOfWorkFactory
    :param fx_asset_store: Local asset store of the test.
    :type fx_asset_store: LocalAssetStore
    :returns: Function taking what the pages are found to show, in the order they are read, and returning the kit and
              the fake processor.
    :rtype: ProbeKitFactory
    """

    def build(answers: list[ContentType | None]) -> tuple[ProcessingKit, FakeContentProbe]:
        """Build the kit over the database and the assets of the test, with a probe that gives the answers.

        :param answers: What the pages are found to show, in the order they are read.
        :type answers: list[ContentType | None]
        :returns: The kit and the fake processor.
        :rtype: tuple[ProcessingKit, FakeContentProbe]
        """
        probe = FakeContentProbe(answers)
        return ProcessingKit(fx_database, fx_uow, fx_asset_store, processors=[SplitNone(), probe]), probe

    return build


def pages_of(kit: ProcessingKit) -> PageService:
    """Build the page service over a new unit of work and the board, the events and the clock of the kit.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The service.
    :rtype: PageService
    """
    return make_page_service(kit.uow(), kit.assets, (kit.events, kit.clock, kit.board))


async def book_of(kit: ProcessingKit, kinds: list[PageKind]) -> tuple[Actor, BookRecord, list[Page]]:
    """Seed a project with a page of each kind, every one with its scan and a ready base version.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param kinds: Kind of each page, in book order.
    :type kinds: list[PageKind]
    :returns: The actor, the project and its pages.
    :rtype: tuple[Actor, BookRecord, list[Page]]
    """
    actor, book_record = await kit.seed_record()
    pages: list[Page] = []
    for index, kind in enumerate(kinds):
        page, _ = await kit.seed_scan_page(book_record, order_key=f'a{index}', kind=kind)
        await kit.seed_base_version(book_record.id, page)
        pages.append(page)
    return actor, book_record, pages


async def keep(
    kit: ProcessingKit, book_id: BookId, page: Page, content_type: ContentType, *, by_hand: bool = False
) -> Page:
    """Write a content type into a stored page, as an earlier detection or the user would have.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_id: Identifier of the project.
    :type book_id: BookId
    :param page: Page to change.
    :type page: Page
    :param content_type: What the page shows.
    :type content_type: ContentType
    :param by_hand: Whether the user set it.
    :type by_hand: bool
    :returns: The page as stored.
    :rtype: Page
    """
    return await kit.uow().change_book(
        book_id,
        lambda repos, _book_record: repos.pages.update(
            evolve(repos.pages.get(page.id), content_type=content_type, content_by_hand=by_hand)
        ),
    )


async def geometry_records(kit: ProcessingKit, book_record: BookRecord, pages: list[Page]) -> None:
    """Store a fresh record of the Geometry stage for each of the pages.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_record: Project owning the pages.
    :type book_record: BookRecord
    :param pages: Pages that have been through the Geometry stage.
    :type pages: list[Page]
    """

    def save_records(repos: BookRepositories, _book_record: BookRecord) -> None:
        """Save the records.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :param _book_record: Project of the book, which the work does not need.
        :type _book_record: BookRecord
        """
        for page in pages:
            repos.page_stages.save(make_page_stage(page_id=page.id, stage=Stage.GEOMETRY))

    await kit.uow().change_book(book_record.id, save_records)


async def detect(
    kit: ProcessingKit, actor: Actor, book_record: BookRecord, page_ids: list[PageId] | None = None
) -> Job:
    """Ask for the detection and let the board run it.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param actor: Account owning the project.
    :type actor: Actor
    :param book_record: Project whose pages are detected.
    :type book_record: BookRecord
    :param page_ids: The pages to detect, or None for those that are not detected yet.
    :type page_ids: list[PageId] | None
    :returns: The job as it stands when the board is done.
    :rtype: Job
    """
    job = await pages_of(kit).start_detection(actor, book_record.id, page_ids)
    await kit.work_queue()
    return stored_job(kit, job.id)


async def stored(kit: ProcessingKit, book_id: BookId, page: Page) -> Page:
    """Read a page as committed.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_id: Identifier of the project.
    :type book_id: BookId
    :param page: The page.
    :type page: Page
    :returns: The page as the database holds it now.
    :rtype: Page
    """
    return await kit.uow().read_book(book_id, lambda repos: repos.pages.get(page.id))


async def geometry_state(kit: ProcessingKit, book_id: BookId, page: Page) -> StageState:
    """Read the state of the record of the Geometry stage of a page as committed.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_id: Identifier of the project.
    :type book_id: BookId
    :param page: The page.
    :type page: Page
    :returns: The state of the record.
    :rtype: StageState
    """
    return (
        await kit.uow().read_book(book_id, lambda repos: repos.page_stages.get(PageStageKey(page.id, Stage.GEOMETRY)))
    ).state


class TestDetectionJob:
    """Tests for the ``detect-content`` job."""

    async def test_what_the_processor_finds_is_written_into_each_page(self, fx_probe_kit: ProbeKitFactory) -> None:
        """Verify each page gets its own answer as a proposal, and the job ends well.

        :param fx_probe_kit: Function building the processing kit with the fake processor.
        :type fx_probe_kit: ProbeKitFactory
        """
        kit, probe = fx_probe_kit(list(ANSWERS))
        actor, book_record, pages = await book_of(kit, [PageKind.TEXT, PageKind.TEXT, PageKind.TITLE])

        job = await detect(kit, actor, book_record)

        found = [await stored(kit, book_record.id, page) for page in pages]
        expect((job.kind, job.state) == (JobKind.DETECT_CONTENT, JobState.SUCCEEDED))
        expect((job.progress.done, job.progress.total) == (len(pages), len(pages)))
        expect([page.content_type for page in found] == list(ANSWERS))
        expect(not any(page.content_by_hand for page in found))
        expect([page.content_source for page in found] == [ContentSource.DETECTED] * len(pages))
        expect(probe.reads == len(pages))
        assert_expectations()

    async def test_a_job_that_names_no_pages_goes_over_the_pages_that_are_not_detected_and_not_a_role(
        self, fx_probe_kit: ProbeKitFactory
    ) -> None:
        """Verify a page that has a type, a page the user set, a cover, a blank page and a placeholder are left alone.

        :param fx_probe_kit: Function building the processing kit with the fake processor.
        :type fx_probe_kit: ProbeKitFactory
        """
        kit, probe = fx_probe_kit([ContentType.COLOR_PICTURE])
        kinds = [PageKind.TEXT, PageKind.TEXT, PageKind.TEXT, PageKind.COVER, PageKind.BLANK]
        actor, book_record, pages = await book_of(kit, kinds)
        found = await keep(kit, book_record.id, pages[1], ContentType.TEXT)
        by_hand = await keep(kit, book_record.id, pages[2], ContentType.BW_PICTURE, by_hand=True)
        await kit.uow().change_book(
            book_record.id, lambda repos, _book_record: repos.pages.add(make_page(order_key='a9'))
        )

        await detect(kit, actor, book_record)

        expect((await stored(kit, book_record.id, pages[0])).content_type is ContentType.COLOR_PICTURE)
        expect(await stored(kit, book_record.id, pages[1]) == found)
        expect(await stored(kit, book_record.id, pages[2]) == by_hand)
        expect([(await stored(kit, book_record.id, page)).content_type for page in pages[3:]] == [None, None])
        expect(probe.reads == 1)
        assert_expectations()

    async def test_a_job_that_names_pages_goes_over_those_whatever_they_have_and_gives_them_back_to_the_detection(
        self, fx_probe_kit: ProbeKitFactory
    ) -> None:
        """Verify a page the user set and a cover that are named are detected, and the first is no longer by hand.

        :param fx_probe_kit: Function building the processing kit with the fake processor.
        :type fx_probe_kit: ProbeKitFactory
        """
        kit, probe = fx_probe_kit([ContentType.TEXT, ContentType.BW_PICTURE])
        actor, book_record, pages = await book_of(kit, [PageKind.TEXT, PageKind.COVER, PageKind.TEXT])
        await keep(kit, book_record.id, pages[0], ContentType.COLOR_PICTURE, by_hand=True)

        job = await detect(kit, actor, book_record, [pages[0].id, pages[1].id])

        first, cover, other = [await stored(kit, book_record.id, page) for page in pages]
        expect(job.state is JobState.SUCCEEDED)
        expect((first.content_type, first.content_by_hand) == (ContentType.TEXT, False))
        expect((cover.content_type, cover.content_by_hand) == (ContentType.BW_PICTURE, False))
        expect(other.content_type is None)
        expect(probe.reads == 2)
        assert_expectations()

    async def test_a_page_that_cannot_be_read_fails_for_itself_and_the_job_goes_on(
        self, fx_probe_kit: ProbeKitFactory
    ) -> None:
        """Verify the page whose image the processor cannot read keeps no type, and the next page is detected.

        :param fx_probe_kit: Function building the processing kit with the fake processor.
        :type fx_probe_kit: ProbeKitFactory
        """
        kit, _ = fx_probe_kit([None, ContentType.BW_PICTURE])
        actor, book_record, pages = await book_of(kit, [PageKind.TEXT, PageKind.TEXT])

        job = await detect(kit, actor, book_record)

        expect(job.state is JobState.SUCCEEDED)
        expect(
            [(await stored(kit, book_record.id, page)).content_type for page in pages] == [None, ContentType.BW_PICTURE]
        )
        assert_expectations()

    async def test_a_job_that_reads_no_page_fails_with_a_reason(self, fx_probe_kit: ProbeKitFactory) -> None:
        """Verify a job whose every page failed ends failed, which the activity of the book shows.

        :param fx_probe_kit: Function building the processing kit with the fake processor.
        :type fx_probe_kit: ProbeKitFactory
        """
        kit, _ = fx_probe_kit([None, None])
        actor, book_record, _ = await book_of(kit, [PageKind.TEXT, PageKind.TEXT])

        job = await detect(kit, actor, book_record)

        expect(job.state is JobState.FAILED)
        expect('no page' in job.error)
        assert_expectations()

    async def test_a_machine_without_the_processor_leaves_the_pages_to_their_kind(
        self, fx_database: SqlDatabase, fx_uow: UnitOfWorkFactory, fx_asset_store: LocalAssetStore
    ) -> None:
        """Verify a catalogue without the processor, as a machine without OpenCV has, gives a job that reads nothing.

        :param fx_database: Database of the test.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        """
        kit = ProcessingKit(fx_database, fx_uow, fx_asset_store, processors=[SplitNone()])
        actor, book_record, pages = await book_of(kit, [PageKind.TEXT])

        job = await detect(kit, actor, book_record)

        expect(job.state is JobState.SUCCEEDED)
        expect((await stored(kit, book_record.id, pages[0])).content_type is None)
        assert_expectations()

    async def test_a_page_without_a_ready_image_is_left_for_the_next_job(self, fx_probe_kit: ProbeKitFactory) -> None:
        """Verify a page whose base version is not made yet is not read and has no type, though the job ends well.

        :param fx_probe_kit: Function building the processing kit with the fake processor.
        :type fx_probe_kit: ProbeKitFactory
        """
        kit, probe = fx_probe_kit([ContentType.TEXT])
        actor, book_record, _ = await book_of(kit, [PageKind.TEXT])
        waiting, _ = await kit.seed_scan_page(book_record, order_key='a5')

        job = await detect(kit, actor, book_record)

        expect(job.state is JobState.SUCCEEDED)
        expect((await stored(kit, book_record.id, waiting)).content_type is None)
        expect(probe.reads == 1)
        assert_expectations()

    async def test_the_stages_of_a_page_whose_content_changed_for_the_steps_go_out_of_date(
        self, fx_probe_kit: ProbeKitFactory
    ) -> None:
        """Verify the Geometry stage of a page that turns out a picture is stale, and that of a text page is not.

        :param fx_probe_kit: Function building the processing kit with the fake processor.
        :type fx_probe_kit: ProbeKitFactory
        """
        kit, _ = fx_probe_kit([ContentType.BW_PICTURE, ContentType.TEXT])
        actor, book_record, pages = await book_of(kit, [PageKind.TEXT, PageKind.TEXT])
        await geometry_records(kit, book_record, pages)

        await detect(kit, actor, book_record)

        states = [await geometry_state(kit, book_record.id, page) for page in pages]
        assert states == [StageState.STALE, StageState.FRESH]

    async def test_a_job_that_was_cancelled_reads_nothing(self, fx_probe_kit: ProbeKitFactory) -> None:
        """Verify a job cancelled while it was queued is dropped by the board and leaves the pages as they are.

        :param fx_probe_kit: Function building the processing kit with the fake processor.
        :type fx_probe_kit: ProbeKitFactory
        """
        kit, probe = fx_probe_kit([ContentType.TEXT])
        actor, book_record, pages = await book_of(kit, [PageKind.TEXT])
        job = await pages_of(kit).start_detection(actor, book_record.id, None)
        await kit.board.cancel(job.id)

        await kit.work_queue()

        expect(probe.reads == 0)
        expect((await stored(kit, book_record.id, pages[0])).content_type is None)
        assert_expectations()


class TestStartDetection:
    """Tests for the requests that queue a detection."""

    async def test_the_request_queues_a_job_that_names_the_pages(self, fx_probe_kit: ProbeKitFactory) -> None:
        """Verify the job is queued with the pages as its parameters, and put on the board.

        :param fx_probe_kit: Function building the processing kit with the fake processor.
        :type fx_probe_kit: ProbeKitFactory
        """
        kit, _ = fx_probe_kit([])
        actor, book_record, pages = await book_of(kit, [PageKind.TEXT, PageKind.TEXT])

        job = await pages_of(kit).start_detection(actor, book_record.id, [pages[1].id])

        expect((job.kind, job.state) == (JobKind.DETECT_CONTENT, JobState.QUEUED))
        expect(ContentDetection.from_map(job.params) == ContentDetection(page_ids=(pages[1].id,)))
        expect([queued.id for queued in kit.board.put_jobs] == [job.id])
        assert_expectations()

    async def test_a_page_that_is_not_in_the_book_is_refused(self, fx_probe_kit: ProbeKitFactory) -> None:
        """Verify naming a page that does not exist is an error, and no job is stored.

        :param fx_probe_kit: Function building the processing kit with the fake processor.
        :type fx_probe_kit: ProbeKitFactory
        """
        kit, _ = fx_probe_kit([])
        actor, book_record, _ = await book_of(kit, [PageKind.TEXT])

        with pytest.raises(NotFoundError):
            await pages_of(kit).start_detection(actor, book_record.id, [PageId(uuid4())])

        expect(not kit.board.put_jobs)
        assert_expectations()

    async def test_a_second_request_while_one_is_queued_is_refused(self, fx_probe_kit: ProbeKitFactory) -> None:
        """Verify one detection of the project at a time, so the pages of the second request are not silently dropped.

        :param fx_probe_kit: Function building the processing kit with the fake processor.
        :type fx_probe_kit: ProbeKitFactory
        """
        kit, _ = fx_probe_kit([])
        actor, book_record, pages = await book_of(kit, [PageKind.TEXT])
        await pages_of(kit).start_detection(actor, book_record.id, None)

        with pytest.raises(ConflictError, match='being detected'):
            await pages_of(kit).start_detection(actor, book_record.id, [pages[0].id])

    async def test_the_detection_of_new_pages_is_queued_once_while_one_waits(
        self, fx_probe_kit: ProbeKitFactory
    ) -> None:
        """Verify the worker that made images asks for one detection, whatever the number of jobs that ask.

        :param fx_probe_kit: Function building the processing kit with the fake processor.
        :type fx_probe_kit: ProbeKitFactory
        """
        kit, _ = fx_probe_kit([])
        _, book_record, _ = await book_of(kit, [PageKind.TEXT])

        pages_of(kit).detect_new_pages(book_record.id)
        pages_of(kit).detect_new_pages(book_record.id)

        [job] = kit.board.put_jobs
        expect(job.kind is JobKind.DETECT_CONTENT)
        expect(ContentDetection.from_map(job.params) == ContentDetection())
        assert_expectations()


class TestChangeOfContent:
    """Tests for the user setting what a page shows."""

    async def test_the_content_the_user_sets_is_by_hand_and_marks_the_stages_stale(
        self, fx_probe_kit: ProbeKitFactory
    ) -> None:
        """Verify the type is kept as the user's, the page says so, and the stage of the page that read it is stale.

        :param fx_probe_kit: Function building the processing kit with the fake processor.
        :type fx_probe_kit: ProbeKitFactory
        """
        kit, _ = fx_probe_kit([])
        actor, book_record, pages = await book_of(kit, [PageKind.TEXT])
        await geometry_records(kit, book_record, pages)

        overview = await pages_of(kit).update(
            actor, book_record.id, pages[0].id, PageChanges(content_type=ContentType.COLOR_PICTURE)
        )

        expect((overview.page.content_type, overview.page.content_by_hand) == (ContentType.COLOR_PICTURE, True))
        expect(overview.page.content_source is ContentSource.HAND)
        expect(await geometry_state(kit, book_record.id, pages[0]) is StageState.STALE)
        assert_expectations()

    async def test_a_type_that_changes_nothing_for_the_steps_leaves_the_stages_up_to_date(
        self, fx_probe_kit: ProbeKitFactory
    ) -> None:
        """Verify choosing text for a page that was text does not make its stages stale.

        :param fx_probe_kit: Function building the processing kit with the fake processor.
        :type fx_probe_kit: ProbeKitFactory
        """
        kit, _ = fx_probe_kit([])
        actor, book_record, pages = await book_of(kit, [PageKind.TEXT])
        await geometry_records(kit, book_record, pages)

        await pages_of(kit).update(actor, book_record.id, pages[0].id, PageChanges(content_type=ContentType.TEXT))

        assert await geometry_state(kit, book_record.id, pages[0]) is StageState.FRESH


async def delete_the_page(kit: ProcessingKit, book_id: BookId, page: Page) -> None:
    """Delete a page in a change of its own, as a request of the user would while a job reads it.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_id: Identifier of the project.
    :type book_id: BookId
    :param page: Page to delete.
    :type page: Page
    """
    await kit.uow().change_book(book_id, lambda repos, _book_record: repos.pages.delete(page.id))


async def set_the_type_by_hand(kit: ProcessingKit, book_id: BookId, page: Page) -> None:
    """Set a content type by hand, as the user would while a job reads the page.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_id: Identifier of the project.
    :type book_id: BookId
    :param page: Page to change.
    :type page: Page
    """
    await keep(kit, book_id, page, ContentType.BW_PICTURE, by_hand=True)


async def detect_the_same_type(kit: ProcessingKit, book_id: BookId, page: Page) -> None:
    """Store the type the probe is going to find, as another job that read the page first would.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_id: Identifier of the project.
    :type book_id: BookId
    :param page: Page to change.
    :type page: Page
    """
    await keep(kit, book_id, page, ContentType.TEXT)


class TestApplyStep:
    """Tests for the change that writes what a probe found, which reads the page again."""

    @pytest.mark.parametrize(
        ('change', 'kept_type', 'kept_source'),
        [
            pytest.param(delete_the_page, None, None, id='page-deleted'),
            pytest.param(set_the_type_by_hand, ContentType.BW_PICTURE, ContentSource.HAND, id='type-set-by-hand'),
            pytest.param(detect_the_same_type, ContentType.TEXT, ContentSource.DETECTED, id='type-already-detected'),
        ],
    )
    async def test_a_page_that_changed_after_the_probe_is_not_written(
        self,
        fx_probe_kit: ProbeKitFactory,
        monkeypatch: pytest.MonkeyPatch,
        change: Callable[[ProcessingKit, BookId, Page], Awaitable[None]],
        kept_type: ContentType | None,
        kept_source: ContentSource | None,
    ) -> None:
        """Verify a page changed between the probe and the write keeps the change, the job counts the page as read.

        The change is made right after the probe returns, so the write finds the page as the change left it, and the
        job writes nothing and announces no page.

        :param fx_probe_kit: Function building the processing kit with the fake processor.
        :type fx_probe_kit: ProbeKitFactory
        :param monkeypatch: Fixture putting the change between the probe and the write.
        :type monkeypatch: pytest.MonkeyPatch
        :param change: What another request does to the page while the job reads it.
        :type change: Callable[[ProcessingKit, BookId, Page], Awaitable[None]]
        :param kept_type: The content type the page holds afterwards, or None for a page that is gone.
        :type kept_type: ContentType | None
        :param kept_source: Where the type the page holds afterwards comes from, or None for a page that is gone.
        :type kept_source: ContentSource | None
        """
        kit, _ = fx_probe_kit([ContentType.TEXT])
        actor, book_record, pages = await book_of(kit, [PageKind.TEXT])
        probe = ContentDetector._probe

        async def probe_then_change(self: ContentDetector, book_id: BookId, base: PageVersion) -> ContentType:
            """Probe the image, then let another request change the page before the job writes its answer.

            :param self: The detector that probes.
            :type self: ContentDetector
            :param book_id: Project owning the page.
            :type book_id: BookId
            :param base: The base version of the page.
            :type base: PageVersion
            :returns: What the probe found.
            :rtype: ContentType
            """
            found = await probe(self, book_id, base)
            await change(kit, book_record.id, pages[0])
            return found

        monkeypatch.setattr(ContentDetector, '_probe', probe_then_change)

        job = await detect(kit, actor, book_record)

        expect((job.state, job.progress.done, job.progress.total) == (JobState.SUCCEEDED, 1, 1))
        if kept_type is None:
            with pytest.raises(NotFoundError):
                await stored(kit, book_record.id, pages[0])
        else:
            after = await stored(kit, book_record.id, pages[0])
            expect((after.content_type, after.content_source) == (kept_type, kept_source))
        assert_expectations()
