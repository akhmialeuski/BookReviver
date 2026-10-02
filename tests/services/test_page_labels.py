"""Tests for the use cases that edit the fields of a page and number a range of pages, on in-memory persistence."""

from typing import TYPE_CHECKING, NamedTuple

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.domain.changes import PageChanges
from bookreviver.domain.enums import LabelStyle, PageChange, PageKind
from bookreviver.domain.errors import ConcurrentChangeError, ConflictError, NotFoundError, ReversedRangeError
from bookreviver.domain.events import PagesChanged
from bookreviver.domain.values import PageNumbering, SliceRequest
from tests.helpers.builders import EPOCH, make_page, make_project, new_account_id
from tests.helpers.page_services import make_page_service
from tests.helpers.seeding import commit_project

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime

    from bookreviver.adapters.clock.system import FixedClock
    from bookreviver.adapters.jobs.recording import RecordingJobQueue
    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.adapters.storage import LocalAssetStore
    from bookreviver.domain.entities import Actor, Page, Project
    from bookreviver.domain.ids import PageId
    from bookreviver.services.pages import PageService
    from tests.helpers.fakes_jobs import RecordingEventBus

pytestmark = pytest.mark.anyio

LATER: datetime = EPOCH.replace(year=EPOCH.year + 1)
STALE_LABEL: str = 'old'
RIVAL_LABEL: str = 'rival'
ROMAN_LIMIT: int = 3999
# Kind and inclusion of the seven pages of the book, which the numbering has to tell apart
LAYOUT: list[tuple[PageKind, bool]] = [
    (PageKind.COVER, True),
    (PageKind.TITLE, True),
    (PageKind.TEXT, True),
    (PageKind.PLATE, True),
    (PageKind.TEXT, True),
    (PageKind.TEXT, False),
    (PageKind.TEXT, True),
]


class NumberingCase(NamedTuple):
    """A style of numbering and the labels it writes into the pages that are numbered.

    :ivar style: Style of the numbers.
    :ivar bracketed: Whether the labels are enclosed in square brackets.
    :ivar labels: The labels of the numbers 10 to 13, which the four pages that are numbered get.
    """

    style: LabelStyle
    bracketed: bool
    labels: list[str]


NUMBERING_CASES: list[NumberingCase] = [
    NumberingCase(style=LabelStyle.ARABIC, bracketed=False, labels=['10', '11', '12', '13']),
    NumberingCase(style=LabelStyle.ARABIC, bracketed=True, labels=['[10]', '[11]', '[12]', '[13]']),
    NumberingCase(style=LabelStyle.ROMAN_LOWER, bracketed=False, labels=['x', 'xi', 'xii', 'xiii']),
    NumberingCase(style=LabelStyle.ROMAN_LOWER, bracketed=True, labels=['[x]', '[xi]', '[xii]', '[xiii]']),
    NumberingCase(style=LabelStyle.ROMAN_UPPER, bracketed=False, labels=['X', 'XI', 'XII', 'XIII']),
    NumberingCase(style=LabelStyle.ROMAN_UPPER, bracketed=True, labels=['[X]', '[XI]', '[XII]', '[XIII]']),
    NumberingCase(style=LabelStyle.NONE, bracketed=False, labels=['', '', '', '']),
    NumberingCase(style=LabelStyle.NONE, bracketed=True, labels=['', '', '', '']),
]


async def _commit_book(database: InMemoryDatabase, owner: Actor) -> tuple[Project, list[Page]]:
    """Commit a book of seven pages of the kinds in ``LAYOUT``, each labelled with a stale number.

    :param database: In-memory database to commit into.
    :type database: InMemoryDatabase
    :param owner: Account owning the project.
    :type owner: Actor
    :returns: The project and its pages in book order.
    :rtype: tuple[Project, list[Page]]
    """
    project = make_project(owner_id=owner.account_id)
    keys = FractionalOrderKeys().spread(lower=None, upper=None, count=len(LAYOUT))
    pages = [
        evolve(make_page(project_id=project.id, order_key=key), kind=kind, included=included, label=STALE_LABEL)
        for key, (kind, included) in zip(keys, LAYOUT, strict=True)
    ]
    await commit_project(database, project, *pages)
    return project, pages


def _labels(database: InMemoryDatabase, pages: list[Page]) -> list[str]:
    """Read the committed labels of pages.

    :param database: In-memory database to read.
    :type database: InMemoryDatabase
    :param pages: Pages to read the labels of.
    :type pages: list[Page]
    :returns: The label of each page as committed, in the order given.
    :rtype: list[str]
    """
    return [database.tables.pages[page.id].label for page in pages]


def _written(database: InMemoryDatabase, before: dict[PageId, Page]) -> set[PageId]:
    """Find the pages whose rows were written since ``before``, which the frozen rows tell by their identity.

    :param database: In-memory database to compare.
    :type database: InMemoryDatabase
    :param before: The committed page rows as they were.
    :type before: dict[PageId, Page]
    :returns: Identifiers of the pages whose row is another object now.
    :rtype: set[PageId]
    """
    return {page_id for page_id, page in database.tables.pages.items() if before[page_id] is not page}


class TestUpdate:
    """Tests for PageService.update()."""

    async def test_changes_the_four_fields_and_writes_one_row(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_actor: Actor,
        fx_clock: FixedClock,
        fx_events: RecordingEventBus,
    ) -> None:
        """Verify the number, the kind, the inclusion and the notes change together, stamped, announced once.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_clock: Clock the service stamps pages with.
        :type fx_clock: FixedClock
        :param fx_events: Recording event bus of the test.
        :type fx_events: RecordingEventBus
        """
        project, pages = await _commit_book(fx_database, fx_actor)
        before = dict(fx_database.tables.pages)
        fx_clock.moment = LATER

        updated = await fx_service().update(
            fx_actor,
            project.id,
            pages[2].id,
            PageChanges(label='[4]', kind=PageKind.OTHER, included=False, notes='Library stamp'),
        )

        stored = fx_database.tables.pages[pages[2].id]
        expect(
            (stored.label, stored.kind, stored.included, stored.notes)
            == ('[4]', PageKind.OTHER, False, 'Library stamp')
        )
        expect(stored.updated_at == LATER)
        expect((stored.id, stored.order_key, stored.scan_id) == (pages[2].id, pages[2].order_key, pages[2].scan_id))
        expect(updated.page == stored)
        expect(updated.position == 2)
        expect(_written(fx_database, before) == {pages[2].id})
        expect(
            fx_events.published
            == [PagesChanged(project_id=project.id, page_ids=[pages[2].id], change=PageChange.EDITED)]
        )
        assert_expectations()

    async def test_a_field_left_as_none_keeps_its_value(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify changing the label alone leaves the kind, the inclusion and the notes of the page as they were.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project, pages = await _commit_book(fx_database, fx_actor)

        await fx_service().update(fx_actor, project.id, pages[3].id, PageChanges(label=''))

        stored = fx_database.tables.pages[pages[3].id]
        assert evolve(stored, label=STALE_LABEL, updated_at=EPOCH, revision=0) == pages[3]
        assert stored.label == ''

    async def test_missing_page_page_of_another_project_and_foreign_project_are_not_found(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify a page that does not exist, one of another project, and a project of another account are all 404.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project, pages = await _commit_book(fx_database, fx_actor)
        stranger, strangers_pages = await _commit_book(fx_database, evolve(fx_actor, account_id=new_account_id()))
        other = make_page(project_id=project.id, order_key='z0')

        with pytest.raises(NotFoundError):
            await fx_service().update(fx_actor, project.id, other.id, PageChanges(label='1'))
        with pytest.raises(NotFoundError):
            await fx_service().update(fx_actor, project.id, strangers_pages[0].id, PageChanges(label='1'))
        with pytest.raises(NotFoundError):
            await fx_service().update(fx_actor, stranger.id, strangers_pages[0].id, PageChanges(label='1'))
        assert _labels(fx_database, pages) == [STALE_LABEL] * len(pages)


class TestNumber:
    """Tests for PageService.number()."""

    @pytest.mark.parametrize(
        'case', NUMBERING_CASES, ids=[f'{case.style}-{case.bracketed}' for case in NUMBERING_CASES]
    )
    async def test_writes_every_style_with_and_without_brackets_skipping_what_is_not_counted(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_actor: Actor,
        case: NumberingCase,
    ) -> None:
        """Verify the labels of a style, from 10, skipping the plate and the page kept out, which keep their label.

        The cover is skipped as well, so the numbered pages are the title page, two text pages and the last text page.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param case: Style, brackets and the labels the first three numbered pages get.
        :type case: NumberingCase
        """
        project, pages = await _commit_book(fx_database, fx_actor)
        numbering = PageNumbering(
            first_page_id=pages[0].id,
            last_page_id=pages[-1].id,
            style=case.style,
            start=10,
            bracketed=case.bracketed,
            skip_kinds=frozenset({PageKind.COVER, PageKind.PLATE}),
        )

        await fx_service().number(fx_actor, project.id, numbering)

        # The cover, the plate and the page kept out keep their label; the other four pages take 10 to 13
        first, second, third, fourth = case.labels
        assert _labels(fx_database, pages) == [STALE_LABEL, first, second, STALE_LABEL, third, STALE_LABEL, fourth]

    async def test_pages_outside_the_range_keep_their_label_and_only_changed_rows_are_written(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_actor: Actor,
        fx_events: RecordingEventBus,
    ) -> None:
        """Verify a range in the middle leaves the other pages alone, and a page already right is not written.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_events: Recording event bus of the test.
        :type fx_events: RecordingEventBus
        """
        project, pages = await _commit_book(fx_database, fx_actor)
        uow = InMemoryUnitOfWork(fx_database)
        await uow.pages.update(evolve(pages[2], label='1'))
        await uow.commit()
        before = dict(fx_database.tables.pages)

        await fx_service().number(
            fx_actor,
            project.id,
            PageNumbering(first_page_id=pages[2].id, last_page_id=pages[4].id, style=LabelStyle.ARABIC),
        )

        expect(_labels(fx_database, pages) == [STALE_LABEL, STALE_LABEL, '1', '2', '3', STALE_LABEL, STALE_LABEL])
        expect(_written(fx_database, before) == {pages[3].id, pages[4].id})
        expect(
            fx_events.published
            == [PagesChanged(project_id=project.id, page_ids=[pages[3].id, pages[4].id], change=PageChange.EDITED)]
        )
        assert_expectations()

    async def test_a_range_of_one_page_numbers_that_page(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify the first page of the range may be its last.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project, pages = await _commit_book(fx_database, fx_actor)

        await fx_service().number(
            fx_actor,
            project.id,
            PageNumbering(first_page_id=pages[4].id, last_page_id=pages[4].id, style=LabelStyle.ROMAN_LOWER, start=7),
        )

        assert _labels(fx_database, pages)[4] == 'vii'

    async def test_range_that_runs_backwards_conflicts_and_changes_nothing(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_actor: Actor,
        fx_events: RecordingEventBus,
    ) -> None:
        """Verify a first page after the last page is a conflict, and no label and no event comes of it.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_events: Recording event bus of the test.
        :type fx_events: RecordingEventBus
        """
        project, pages = await _commit_book(fx_database, fx_actor)

        with pytest.raises(ReversedRangeError, match='from a later page to an earlier one'):
            await fx_service().number(
                fx_actor,
                project.id,
                PageNumbering(first_page_id=pages[4].id, last_page_id=pages[1].id, style=LabelStyle.ARABIC),
            )

        expect(_labels(fx_database, pages) == [STALE_LABEL] * len(pages))
        expect(fx_events.published == [])
        assert_expectations()

    async def test_numbers_past_the_roman_limit_conflict_and_change_nothing(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify a range that runs on to 4000 in Roman numerals is refused before any page is written.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project, pages = await _commit_book(fx_database, fx_actor)

        with pytest.raises(ConflictError, match=str(ROMAN_LIMIT + 1)):
            await fx_service().number(
                fx_actor,
                project.id,
                PageNumbering(
                    first_page_id=pages[0].id,
                    last_page_id=pages[2].id,
                    style=LabelStyle.ROMAN_UPPER,
                    start=ROMAN_LIMIT - 1,
                ),
            )

        assert _labels(fx_database, pages) == [STALE_LABEL] * len(pages)

    async def test_page_of_another_project_is_not_found(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify a range whose last page belongs to another project is answered not found.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project, pages = await _commit_book(fx_database, fx_actor)
        _, others = await _commit_book(fx_database, fx_actor)

        with pytest.raises(NotFoundError):
            await fx_service().number(
                fx_actor,
                project.id,
                PageNumbering(first_page_id=pages[0].id, last_page_id=others[-1].id, style=LabelStyle.ARABIC),
            )

        manifest = await fx_service().manifest(fx_actor, project.id, SliceRequest())
        assert [overview.page.label for overview in manifest.items] == [STALE_LABEL] * len(pages)


class TestConcurrentWrites:
    """Tests for the use cases that write pages while another request commits a change to one of them."""

    @staticmethod
    def _racing_service(
        database: InMemoryDatabase,
        runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue],
        assets: LocalAssetStore,
        monkeypatch: pytest.MonkeyPatch,
        rival: Callable[[], Awaitable[None]],
    ) -> PageService:
        """Build the page service over a unit of work whose page reads are followed by the rival's commit.

        The unit of work builds new repositories when it rolls back, so the rival races the first attempt only.

        :param database: In-memory database shared with the rival.
        :type database: InMemoryDatabase
        :param runtime: The recording bus, the clock and the queue the service reports through.
        :type runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue]
        :param assets: Asset store of the test.
        :type assets: LocalAssetStore
        :param monkeypatch: Fixture that restores the patched repository after the test.
        :type monkeypatch: pytest.MonkeyPatch
        :param rival: Request committing a change to a page, run after each read of one page.
        :type rival: Callable[[], Awaitable[None]]
        :returns: The service of one request.
        :rtype: PageService
        """
        uow = InMemoryUnitOfWork(database)
        read = uow.pages.get

        async def get_then_race(page_id: PageId) -> Page:
            """Read a page, then let the rival commit a change of it.

            :param page_id: Identifier of the page.
            :type page_id: PageId
            :returns: The page as it was before the rival wrote.
            :rtype: Page
            """
            page = await read(page_id)
            await rival()
            return page

        monkeypatch.setattr(uow.pages, 'get', get_then_race)
        return make_page_service(uow, assets, runtime)

    async def test_an_edit_of_another_field_made_between_the_read_and_the_write_is_kept(
        self,
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue],
        fx_actor: Actor,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify the kind the service sets is applied to the page the rival has just labelled, so both edits stay.

        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_runtime: The recording bus, the clock and the queue the service reports through.
        :type fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue]
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param monkeypatch: Fixture that restores the patched repository after the test.
        :type monkeypatch: pytest.MonkeyPatch
        """
        project, pages = await _commit_book(fx_database, fx_actor)
        writes = 0

        async def rival() -> None:
            """Commit a new label of the page the service reads, once."""
            nonlocal writes
            if writes:
                return
            writes += 1
            uow = InMemoryUnitOfWork(fx_database)
            await uow.pages.update(evolve(await uow.pages.get(pages[2].id), label=RIVAL_LABEL))
            await uow.commit()

        updated = await self._racing_service(fx_database, fx_runtime, fx_asset_store, monkeypatch, rival).update(
            fx_actor, project.id, pages[2].id, PageChanges(kind=PageKind.OTHER)
        )

        stored = fx_database.tables.pages[pages[2].id]
        expect((stored.label, stored.kind) == (RIVAL_LABEL, PageKind.OTHER))
        expect(updated.page == stored)
        expect(stored.revision == 2)
        assert_expectations()

    async def test_a_numbering_over_a_page_changed_meanwhile_conflicts_and_writes_nothing(
        self,
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue],
        fx_actor: Actor,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify the numbering is refused with the sentence for the person, and the page before the changed one is unwritten.

        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_runtime: The recording bus, the clock and the queue the service reports through.
        :type fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue]
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param monkeypatch: Fixture that restores the patched repository after the test.
        :type monkeypatch: pytest.MonkeyPatch
        """
        project, pages = await _commit_book(fx_database, fx_actor)
        events, _, _ = fx_runtime

        async def rival() -> None:
            """Commit a new label of the last page of the range, after the service has read the range."""
            uow = InMemoryUnitOfWork(fx_database)
            await uow.pages.update(evolve(await uow.pages.get(pages[4].id), label=RIVAL_LABEL))
            await uow.commit()

        service = self._racing_service(fx_database, fx_runtime, fx_asset_store, monkeypatch, rival)
        numbering = PageNumbering(first_page_id=pages[2].id, last_page_id=pages[4].id, style=LabelStyle.ARABIC)

        with pytest.raises(ConcurrentChangeError, match='The pages changed while this ran'):
            await service.number(fx_actor, project.id, numbering)

        expect(_labels(fx_database, pages) == [*([STALE_LABEL] * 4), RIVAL_LABEL, STALE_LABEL, STALE_LABEL])
        expect(events.published == [])
        assert_expectations()
