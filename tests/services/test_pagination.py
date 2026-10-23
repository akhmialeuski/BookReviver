"""Tests for the pagination sections, the labels computed from them, and their recompute in the use cases of the pages."""

from typing import TYPE_CHECKING, Any, NamedTuple

import anyio
import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
from bookreviver.domain.changes import PageChanges
from bookreviver.domain.enums import BookResource, LabelStyle, NewPageOrigin, NumberDisplay, PageKind, Side
from bookreviver.domain.errors import ConflictError, NotFoundError, ReversedRangeError
from bookreviver.domain.values import (
    NewPage,
    NumberedPage,
    PageAnchor,
    PageNumbering,
    PaginationSectionDraft,
    SliceRequest,
)
from bookreviver.services.page_labels import PageLabels
from tests.helpers.book_hold import hold_book_in_thread
from tests.helpers.builders import make_page, make_project, new_account_id
from tests.helpers.seeding import ensure_account, store_project

if TYPE_CHECKING:
    from collections.abc import Callable

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import Actor, Page, PaginationSection, Project
    from bookreviver.domain.ids import ProjectId
    from bookreviver.ports.persistence import BookRepositories, UnitOfWork
    from bookreviver.services.pages import PageService
    from bookreviver.services.pagination import PaginationService
    from tests.helpers.database import UnitOfWorkFactory
    from tests.helpers.fakes_jobs import RecordingEventBus
    from tests.helpers.page_writes import PageWrites

pytestmark = pytest.mark.anyio

# The Roman labels the second, third and fourth page of a book get
ROMAN_2: str = 'ii'
ROMAN_3: str = 'iii'
ROMAN_4: str = 'iv'
SECTION_NAME: str = 'Text'
STALE_LABEL: str = 'old'
HAND_WRITTEN: str = '12a'
ROMAN_LIMIT: int = 3999
FIRST_NUMBER: int = 10
PLATE_PREFIX: str = 'Plate '
FIRST_PLATE: str = 'Plate 1'
CLASH_MESSAGE: str = 'starts at this page already'
PLATES: frozenset[PageKind] = frozenset({PageKind.PLATE, PageKind.FRONTISPIECE})
TEXT: PageKind = PageKind.TEXT
# Kind and inclusion of the seven pages of the book the numbering tests start from
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
    NumberingCase(style=LabelStyle.ROMAN_UPPER, bracketed=True, labels=['[X]', '[XI]', '[XII]', '[XIII]']),
    NumberingCase(style=LabelStyle.ALPHA_LOWER, bracketed=False, labels=['j', 'k', 'l', 'm']),
    NumberingCase(style=LabelStyle.NONE, bracketed=False, labels=['', '', '', '']),
]


async def _commit_book(
    uow: UnitOfWork, owner: Actor, *layout: tuple[PageKind, bool], label: str = ''
) -> tuple[Project, list[Page]]:
    """Commit a book of pages of the given kinds and inclusion, all labelled by hand with ``label`` when it is given.

    :param uow: Unit of work to commit through, over a database that stores the account of the owner.
    :type uow: UnitOfWork
    :param owner: Account owning the project.
    :type owner: Actor
    :param layout: Kind and inclusion of each page in book order.
    :type layout: tuple[PageKind, bool]
    :param label: Label every page gets as an exception, or empty for pages that are not labelled.
    :type label: str
    :returns: The project and its pages in book order.
    :rtype: tuple[Project, list[Page]]
    """
    project = make_project()
    keys = FractionalOrderKeys().spread(lower=None, upper=None, count=len(layout))
    pages = [
        evolve(
            make_page(order_key=key),
            kind=kind,
            included=included,
            manual_label=label or None,
        )
        for key, (kind, included) in zip(keys, layout, strict=True)
    ]
    await store_project(uow, project, *pages, owner=owner.account_id)
    return project, pages


def _text_book(count: int) -> tuple[tuple[PageKind, bool], ...]:
    """Describe a book of ``count`` included text pages.

    :param count: Number of pages.
    :type count: int
    :returns: The kind and inclusion of each page.
    :rtype: tuple[tuple[PageKind, bool], ...]
    """
    return ((TEXT, True),) * count


async def _labels(uow: UnitOfWork, project_id: ProjectId, pages: list[Page]) -> list[str]:
    """Read the labels pages show, as the sections and the manual labels committed give them on read.

    :param uow: Unit of work to read through.
    :type uow: UnitOfWork
    :param project_id: Identifier of the project of the pages.
    :type project_id: ProjectId
    :param pages: Pages to read the labels of.
    :type pages: list[Page]
    :returns: The label of each page, in the order given.
    :rtype: list[str]
    """

    def read(repos: BookRepositories) -> list[str]:
        """Read the pages as they are stored and number them.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :returns: The label of each page, in the order given.
        :rtype: list[str]
        """
        stored = repos.pages.list_by_ids([page.id for page in pages])
        labels = PageLabels.numbering(repos, stored).labels
        return [labels[page.id] for page in pages]

    return await uow.read_book(project_id, read)


async def _sections(uow: UnitOfWork, project: Project) -> list[PaginationSection]:
    """Read the committed sections of a project in the order they were made.

    :param uow: Unit of work to read through.
    :type uow: UnitOfWork
    :param project: The project.
    :type project: Project
    :returns: The sections of the project.
    :rtype: list[PaginationSection]
    """
    found = await uow.read_book(project.id, lambda repos: repos.pagination_sections.list_all())
    return sorted(found, key=lambda section: (section.created_at, section.id))


def _draft(page: Page, **fields: Any) -> PaginationSectionDraft:
    """State a section that starts at a page, in Arabic numerals unless given another rule.

    :param page: Page the section starts at.
    :type page: Page
    :param fields: Fields of the draft other than the first page.
    :type fields: Any
    :returns: The draft.
    :rtype: PaginationSectionDraft
    """
    merged: dict[str, Any] = {'style': LabelStyle.ARABIC, **fields}
    return PaginationSectionDraft(first_page_id=page.id, **merged)


class TestSections:
    """Tests for the sections of PaginationService and the labels they give."""

    async def test_roman_front_matter_is_followed_by_arabic_text_from_one(
        self,
        fx_pagination: Callable[[], PaginationService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        fx_book_changes: RecordingEventBus,
    ) -> None:
        """Verify the preface is numbered in Roman numerals, the text starts at 1, and each section is one change.

        :param fx_pagination: Function building the service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_book_changes: Where the units of work of the test announce the changes of the books.
        :type fx_book_changes: RecordingEventBus
        """
        project, pages = await _commit_book(fx_uow(), fx_actor, *_text_book(5))

        fx_book_changes.published.clear()
        await fx_pagination().add(fx_actor, project.id, _draft(pages[0], style=LabelStyle.ROMAN_LOWER))
        expect(await _labels(fx_uow(), project.id, pages) == ['i', ROMAN_2, ROMAN_3, ROMAN_4, 'v'])
        await fx_pagination().add(fx_actor, project.id, _draft(pages[2]))

        expect(await _labels(fx_uow(), project.id, pages) == ['i', ROMAN_2, '1', '2', '3'])
        expect([BookResource.PAGES in parts for parts in fx_book_changes.changed_parts()] == [True, True])
        assert_expectations()

    async def test_a_page_counted_and_not_printed_shows_its_number_in_square_brackets(
        self, fx_pagination: Callable[[], PaginationService], fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify the front matter that counts without a printed number is shown as ``[iii]``.

        :param fx_pagination: Function building the service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project, pages = await _commit_book(fx_uow(), fx_actor, *_text_book(5))

        await fx_pagination().add(
            fx_actor, project.id, _draft(pages[0], style=LabelStyle.ROMAN_LOWER, display=NumberDisplay.COUNTED)
        )
        await fx_pagination().add(fx_actor, project.id, _draft(pages[3]))

        assert await _labels(fx_uow(), project.id, pages) == ['[i]', '[ii]', '[iii]', '1', '2']

    async def test_a_page_that_is_not_counted_takes_no_number(
        self, fx_pagination: Callable[[], PaginationService], fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify the pages of a section that does not count are unnumbered and the text counts from its own start.

        :param fx_pagination: Function building the service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project, pages = await _commit_book(fx_uow(), fx_actor, *_text_book(4))

        await fx_pagination().add(fx_actor, project.id, _draft(pages[0], display=NumberDisplay.NOT_COUNTED))
        await fx_pagination().add(fx_actor, project.id, _draft(pages[2]))

        assert await _labels(fx_uow(), project.id, pages) == ['', '', '1', '2']

    async def test_plates_get_their_own_series_without_breaking_the_text_count(
        self, fx_pagination: Callable[[], PaginationService], fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify the plates are numbered Plate I, Plate II across the book and the text counts on without them.

        :param fx_pagination: Function building the service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        layout = [(kind, True) for kind in (PageKind.FRONTISPIECE, TEXT, TEXT, PageKind.PLATE, TEXT, PageKind.PLATE)]
        project, pages = await _commit_book(fx_uow(), fx_actor, *layout)

        await fx_pagination().add(fx_actor, project.id, _draft(pages[0]))
        await fx_pagination().add(
            fx_actor, project.id, _draft(pages[0], style=LabelStyle.ROMAN_UPPER, prefix=PLATE_PREFIX, kinds=PLATES)
        )

        assert await _labels(fx_uow(), project.id, pages) == ['Plate I', '1', '2', 'Plate II', '3', 'Plate III']

    async def test_a_label_written_by_hand_stays_when_a_section_is_made(
        self,
        fx_pagination: Callable[[], PaginationService],
        fx_uow: UnitOfWorkFactory,
        fx_page_writes: PageWrites,
        fx_actor: Actor,
    ) -> None:
        """Verify an exception keeps its label, still counts, and is not written.

        :param fx_pagination: Function building the service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_page_writes: Record of the pages the page repository writes.
        :type fx_page_writes: PageWrites
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project, pages = await _commit_book(fx_uow(), fx_actor, *_text_book(3))
        await fx_uow().change_book(
            project.id,
            lambda repos, _project: repos.pages.update(evolve(pages[1], manual_label=HAND_WRITTEN)),
        )
        fx_page_writes.start()

        await fx_pagination().add(fx_actor, project.id, _draft(pages[0]))

        expect(await _labels(fx_uow(), project.id, pages) == ['1', HAND_WRITTEN, '3'])
        # The numbers of the section are computed on read, so making it writes no page
        expect(fx_page_writes.page_ids == set())
        assert_expectations()

    async def test_no_page_is_written_when_no_label_changes(
        self,
        fx_pagination: Callable[[], PaginationService],
        fx_uow: UnitOfWorkFactory,
        fx_page_writes: PageWrites,
        fx_actor: Actor,
    ) -> None:
        """Verify a series that takes no page of the book writes no row of a page.

        :param fx_pagination: Function building the service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_page_writes: Record of the pages the page repository writes.
        :type fx_page_writes: PageWrites
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project, pages = await _commit_book(fx_uow(), fx_actor, *_text_book(3))
        await fx_pagination().add(fx_actor, project.id, _draft(pages[0]))
        fx_page_writes.start()

        await fx_pagination().add(fx_actor, project.id, _draft(pages[0], kinds=PLATES))

        expect(fx_page_writes.page_ids == set())
        expect(len(await _sections(fx_uow(), project)) == 2)
        assert_expectations()

    async def test_removing_a_section_gives_its_pages_to_the_section_before_it(
        self, fx_pagination: Callable[[], PaginationService], fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify the pages of a removed section fall to the one before it, and to no number when none is left.

        :param fx_pagination: Function building the service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project, pages = await _commit_book(fx_uow(), fx_actor, *_text_book(4))
        preface = await fx_pagination().add(fx_actor, project.id, _draft(pages[0], style=LabelStyle.ROMAN_LOWER))
        body = await fx_pagination().add(fx_actor, project.id, _draft(pages[2]))

        await fx_pagination().remove(fx_actor, project.id, body.id)
        expect(await _labels(fx_uow(), project.id, pages) == ['i', ROMAN_2, ROMAN_3, ROMAN_4])
        await fx_pagination().remove(fx_actor, project.id, preface.id)

        expect(await _labels(fx_uow(), project.id, pages) == [''] * len(pages))
        expect(await _sections(fx_uow(), project) == [])
        assert_expectations()

    async def test_updating_a_section_changes_its_first_page_and_its_rule(
        self, fx_pagination: Callable[[], PaginationService], fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify a section moved to another page and given another first number renumbers the pages around it.

        :param fx_pagination: Function building the service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project, pages = await _commit_book(fx_uow(), fx_actor, *_text_book(5))
        await fx_pagination().add(fx_actor, project.id, _draft(pages[0], style=LabelStyle.ROMAN_LOWER))
        body = await fx_pagination().add(fx_actor, project.id, _draft(pages[2]))

        updated = await fx_pagination().update(
            fx_actor, project.id, body.id, _draft(pages[3], start=5, name=SECTION_NAME)
        )

        expect(await _labels(fx_uow(), project.id, pages) == ['i', ROMAN_2, ROMAN_3, '5', '6'])
        expect(
            (updated.id, updated.first_page_id, updated.start, updated.name) == (body.id, pages[3].id, 5, SECTION_NAME)
        )
        assert_expectations()

    async def test_a_second_section_of_one_flow_cannot_start_at_the_same_page(
        self, fx_pagination: Callable[[], PaginationService], fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify two main-flow sections, and two series that share a kind, clash, while a series and the flow do not.

        :param fx_pagination: Function building the service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project, pages = await _commit_book(fx_uow(), fx_actor, *_text_book(3))
        await fx_pagination().add(fx_actor, project.id, _draft(pages[0]))
        await fx_pagination().add(fx_actor, project.id, _draft(pages[0], kinds=PLATES))

        with pytest.raises(ConflictError, match=CLASH_MESSAGE):
            await fx_pagination().add(fx_actor, project.id, _draft(pages[0], style=LabelStyle.ROMAN_LOWER))
        with pytest.raises(ConflictError, match=CLASH_MESSAGE):
            await fx_pagination().add(fx_actor, project.id, _draft(pages[0], kinds=frozenset({PageKind.PLATE})))

        assert len(await _sections(fx_uow(), project)) == 2

    async def test_a_section_cannot_be_made_from_a_page_or_project_that_is_not_the_actors(
        self,
        fx_pagination: Callable[[], PaginationService],
        fx_database: SqlDatabase,
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
    ) -> None:
        """Verify a page of another project and a project of another account are both answered not found.

        :param fx_pagination: Function building the service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_database: Database of the test.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project, pages = await _commit_book(fx_uow(), fx_actor, *_text_book(2))
        owner = evolve(fx_actor, account_id=new_account_id())
        await ensure_account(fx_database, owner.account_id)
        stranger, strangers_pages = await _commit_book(fx_uow(), owner, *_text_book(1))
        own = await fx_pagination().add(fx_actor, project.id, _draft(pages[0]))

        with pytest.raises(NotFoundError):
            await fx_pagination().add(fx_actor, project.id, _draft(strangers_pages[0]))
        with pytest.raises(NotFoundError):
            await fx_pagination().add(fx_actor, stranger.id, _draft(pages[0]))
        with pytest.raises(NotFoundError):
            await fx_pagination().update(fx_actor, stranger.id, own.id, _draft(pages[1]))
        with pytest.raises(NotFoundError):
            await fx_pagination().remove(fx_actor, stranger.id, own.id)

        assert await _sections(fx_uow(), project) == [own]

    async def test_sections_are_listed_in_book_order_with_the_main_flow_first(
        self, fx_pagination: Callable[[], PaginationService], fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify the sections come in the order of the pages they start at, a series after the flow at one page.

        :param fx_pagination: Function building the service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project, pages = await _commit_book(fx_uow(), fx_actor, *_text_book(4))
        late = await fx_pagination().add(fx_actor, project.id, _draft(pages[3]))
        series = await fx_pagination().add(fx_actor, project.id, _draft(pages[0], kinds=PLATES))
        flow = await fx_pagination().add(fx_actor, project.id, _draft(pages[0]))

        listed = await fx_pagination().sections(fx_actor, project.id, SliceRequest())
        window = await fx_pagination().sections(fx_actor, project.id, SliceRequest(offset=1, limit=1))

        expect(listed.items == [flow, series, late])
        expect(listed.total == len(listed.items))
        expect((window.items, window.total) == ([series], len(listed.items)))
        assert_expectations()

    async def test_numbers_past_the_roman_limit_conflict_and_store_nothing(
        self, fx_pagination: Callable[[], PaginationService], fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify a section that runs on to 4000 in Roman numerals is refused, and its sections and labels are not stored.

        :param fx_pagination: Function building the service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project, pages = await _commit_book(fx_uow(), fx_actor, *_text_book(3))

        with pytest.raises(ConflictError, match=str(ROMAN_LIMIT + 1)):
            await fx_pagination().add(
                fx_actor, project.id, _draft(pages[0], style=LabelStyle.ROMAN_UPPER, start=ROMAN_LIMIT - 1)
            )

        expect(await _sections(fx_uow(), project) == [])
        expect(await _labels(fx_uow(), project.id, pages) == [''] * len(pages))
        assert_expectations()


class TestNumberPages:
    """Tests for PaginationService.number() and preview_numbers(), which make sections from a range of pages."""

    @pytest.mark.parametrize(
        'case', NUMBERING_CASES, ids=[f'{case.style}-{case.bracketed}' for case in NUMBERING_CASES]
    )
    async def test_writes_every_style_with_and_without_brackets_skipping_what_is_not_counted(
        self,
        fx_pagination: Callable[[], PaginationService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        case: NumberingCase,
    ) -> None:
        """Verify the labels of a style, from 10, skipping the plate and the page kept out, which keep their label.

        The cover is skipped as well, so the numbered pages are the title page, two text pages and the last text page.

        :param fx_pagination: Function building the service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param case: Style, brackets and the labels the four numbered pages get.
        :type case: NumberingCase
        """
        project, pages = await _commit_book(fx_uow(), fx_actor, *LAYOUT, label=STALE_LABEL)
        numbering = PageNumbering(
            first_page_id=pages[0].id,
            last_page_id=pages[-1].id,
            style=case.style,
            start=FIRST_NUMBER,
            bracketed=case.bracketed,
            skip_kinds=frozenset({PageKind.COVER, PageKind.PLATE}),
        )

        await fx_pagination().number(fx_actor, project.id, numbering)

        # The cover, the plate and the page kept out keep their label; the other four pages take 10 to 13
        first, second, third, fourth = case.labels
        assert await _labels(fx_uow(), project.id, pages) == [
            STALE_LABEL,
            first,
            second,
            STALE_LABEL,
            third,
            STALE_LABEL,
            fourth,
        ]

    async def test_numbering_a_range_makes_a_section_a_series_for_the_skipped_kinds_and_no_closing_at_the_end(
        self, fx_pagination: Callable[[], PaginationService], fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify a numbering that runs to the end of the book stores its main-flow section and its series, and no more.

        :param fx_pagination: Function building the service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project, pages = await _commit_book(fx_uow(), fx_actor, *LAYOUT)
        skipped = frozenset({PageKind.COVER, PageKind.PLATE})

        await fx_pagination().number(
            fx_actor,
            project.id,
            PageNumbering(
                first_page_id=pages[1].id,
                last_page_id=pages[-1].id,
                style=LabelStyle.ROMAN_LOWER,
                bracketed=True,
                skip_kinds=skipped,
            ),
        )

        flow, series = sorted(await _sections(fx_uow(), project), key=lambda section: section.is_series)
        expect(
            (flow.first_page_id, flow.style, flow.display, flow.kinds)
            == (pages[1].id, LabelStyle.ROMAN_LOWER, NumberDisplay.COUNTED, frozenset())
        )
        expect(
            (series.first_page_id, series.display, series.kinds) == (pages[1].id, NumberDisplay.NOT_COUNTED, skipped)
        )
        assert_expectations()

    async def test_pages_after_the_range_are_closed_with_a_section_that_does_not_count(
        self,
        fx_pagination: Callable[[], PaginationService],
        fx_uow: UnitOfWorkFactory,
        fx_page_writes: PageWrites,
        fx_actor: Actor,
        fx_book_changes: RecordingEventBus,
    ) -> None:
        """Verify a range in the middle leaves the pages outside it alone, and only the pages it numbers are written.

        :param fx_pagination: Function building the service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_page_writes: Record of the pages the page repository writes.
        :type fx_page_writes: PageWrites
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_book_changes: Where the units of work of the test announce the changes of the books.
        :type fx_book_changes: RecordingEventBus
        """
        project, pages = await _commit_book(fx_uow(), fx_actor, *LAYOUT, label=STALE_LABEL)
        fx_page_writes.start()

        fx_book_changes.published.clear()
        await fx_pagination().number(
            fx_actor,
            project.id,
            PageNumbering(first_page_id=pages[2].id, last_page_id=pages[4].id, style=LabelStyle.ARABIC),
        )

        expect(
            await _labels(fx_uow(), project.id, pages)
            == [STALE_LABEL, STALE_LABEL, '1', '2', '3', STALE_LABEL, STALE_LABEL]
        )
        expect(fx_page_writes.page_ids == {pages[2].id, pages[3].id, pages[4].id})
        expect([BookResource.PAGES in parts for parts in fx_book_changes.changed_parts()] == [True])
        by_page = {section.first_page_id: section for section in await _sections(fx_uow(), project)}
        closing = by_page[pages[5].id]
        expect(set(by_page) == {pages[2].id, pages[5].id})
        expect(
            (closing.display, closing.style, closing.kinds) == (NumberDisplay.NOT_COUNTED, LabelStyle.NONE, frozenset())
        )
        assert_expectations()

    async def test_numbering_the_same_range_again_replaces_its_sections_and_gives_up_the_labels_by_hand(
        self, fx_pagination: Callable[[], PaginationService], fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify a second numbering replaces the first, and a label typed in the range is overwritten as it used to be.

        :param fx_pagination: Function building the service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project, pages = await _commit_book(fx_uow(), fx_actor, *_text_book(4))
        first = PageNumbering(first_page_id=pages[0].id, last_page_id=pages[-1].id, style=LabelStyle.ROMAN_LOWER)
        await fx_pagination().number(fx_actor, project.id, first)
        await fx_uow().change_book(
            project.id,
            lambda repos, _project: repos.pages.update(evolve(repos.pages.get(pages[1].id), manual_label=HAND_WRITTEN)),
        )

        await fx_pagination().number(fx_actor, project.id, evolve(first, style=LabelStyle.ARABIC, start=FIRST_NUMBER))

        expect(await _labels(fx_uow(), project.id, pages) == ['10', '11', '12', '13'])
        expect(len(await _sections(fx_uow(), project)) == 1)
        stored = await fx_uow().read_book(
            project.id, lambda repos: repos.pages.list_by_ids([page.id for page in pages])
        )
        expect(len(stored) == len(pages) and all(page.manual_label is None for page in stored))
        assert_expectations()

    async def test_the_sections_that_start_inside_the_range_are_replaced(
        self, fx_pagination: Callable[[], PaginationService], fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify a section inside the range goes, and one before it stays and ends where the range begins.

        :param fx_pagination: Function building the service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project, pages = await _commit_book(fx_uow(), fx_actor, *_text_book(6))
        before = await fx_pagination().add(fx_actor, project.id, _draft(pages[0], style=LabelStyle.ROMAN_LOWER))
        await fx_pagination().add(fx_actor, project.id, _draft(pages[3]))

        await fx_pagination().number(
            fx_actor,
            project.id,
            PageNumbering(first_page_id=pages[2].id, last_page_id=pages[-1].id, style=LabelStyle.ARABIC),
        )

        expect(await _labels(fx_uow(), project.id, pages) == ['i', ROMAN_2, '1', '2', '3', '4'])
        expect(before in await _sections(fx_uow(), project))
        expect(len(await _sections(fx_uow(), project)) == 2)
        assert_expectations()

    async def test_a_range_of_one_page_numbers_that_page(
        self, fx_pagination: Callable[[], PaginationService], fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify the first page of the range may be its last.

        :param fx_pagination: Function building the service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project, pages = await _commit_book(fx_uow(), fx_actor, *LAYOUT, label=STALE_LABEL)

        await fx_pagination().number(
            fx_actor,
            project.id,
            PageNumbering(first_page_id=pages[4].id, last_page_id=pages[4].id, style=LabelStyle.ROMAN_LOWER, start=7),
        )

        assert (await _labels(fx_uow(), project.id, pages))[4] == 'vii'

    async def test_range_that_runs_backwards_conflicts_and_changes_nothing(
        self,
        fx_pagination: Callable[[], PaginationService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        fx_book_changes: RecordingEventBus,
    ) -> None:
        """Verify a first page after the last page is a conflict, and no section, label or event comes of it.

        :param fx_pagination: Function building the service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_book_changes: Where the units of work of the test announce the changes of the books.
        :type fx_book_changes: RecordingEventBus
        """
        project, pages = await _commit_book(fx_uow(), fx_actor, *LAYOUT, label=STALE_LABEL)

        fx_book_changes.published.clear()
        with pytest.raises(ReversedRangeError, match='from a later page to an earlier one'):
            await fx_pagination().number(
                fx_actor,
                project.id,
                PageNumbering(first_page_id=pages[4].id, last_page_id=pages[1].id, style=LabelStyle.ARABIC),
            )

        expect(await _labels(fx_uow(), project.id, pages) == [STALE_LABEL] * len(pages))
        expect((await _sections(fx_uow(), project), fx_book_changes.published) == ([], []))
        assert_expectations()

    async def test_numbers_past_the_roman_limit_conflict_and_change_nothing(
        self,
        fx_pagination: Callable[[], PaginationService],
        fx_uow: UnitOfWorkFactory,
        fx_page_writes: PageWrites,
        fx_actor: Actor,
    ) -> None:
        """Verify a range that runs on to 4000 in Roman numerals is refused before anything is written.

        :param fx_pagination: Function building the service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_page_writes: Record of the pages the page repository writes.
        :type fx_page_writes: PageWrites
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project, pages = await _commit_book(fx_uow(), fx_actor, *LAYOUT, label=STALE_LABEL)
        fx_page_writes.start()

        with pytest.raises(ConflictError, match=str(ROMAN_LIMIT + 1)):
            await fx_pagination().number(
                fx_actor,
                project.id,
                PageNumbering(
                    first_page_id=pages[0].id,
                    last_page_id=pages[2].id,
                    style=LabelStyle.ROMAN_UPPER,
                    start=ROMAN_LIMIT - 1,
                ),
            )

        expect(fx_page_writes.page_ids == set())
        expect(await _sections(fx_uow(), project) == [])
        assert_expectations()

    async def test_page_of_another_project_is_not_found(
        self, fx_pagination: Callable[[], PaginationService], fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify a range whose last page belongs to another project is answered not found.

        :param fx_pagination: Function building the service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project, pages = await _commit_book(fx_uow(), fx_actor, *LAYOUT)
        _, others = await _commit_book(fx_uow(), fx_actor, *LAYOUT)

        with pytest.raises(NotFoundError):
            await fx_pagination().number(
                fx_actor,
                project.id,
                PageNumbering(first_page_id=pages[0].id, last_page_id=others[-1].id, style=LabelStyle.ARABIC),
            )

        assert await _sections(fx_uow(), project) == []

    async def test_the_preview_gives_the_labels_the_numbering_stores_and_writes_nothing(
        self,
        fx_pagination: Callable[[], PaginationService],
        fx_uow: UnitOfWorkFactory,
        fx_page_writes: PageWrites,
        fx_actor: Actor,
        fx_book_changes: RecordingEventBus,
    ) -> None:
        """Verify the preview lists the pages the numbering numbers with the labels it then writes.

        :param fx_pagination: Function building the service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_page_writes: Record of the pages the page repository writes.
        :type fx_page_writes: PageWrites
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_book_changes: Where the units of work of the test announce the changes of the books.
        :type fx_book_changes: RecordingEventBus
        """
        project, pages = await _commit_book(fx_uow(), fx_actor, *LAYOUT, label=STALE_LABEL)
        fx_page_writes.start()
        numbering = PageNumbering(
            first_page_id=pages[0].id,
            last_page_id=pages[-1].id,
            style=LabelStyle.ROMAN_LOWER,
            start=FIRST_NUMBER,
            skip_kinds=frozenset({PageKind.COVER, PageKind.PLATE}),
        )

        fx_book_changes.published.clear()
        previewed = await fx_pagination().preview_numbers(fx_actor, project.id, numbering)
        expect(fx_page_writes.page_ids == set())
        expect((await _sections(fx_uow(), project), fx_book_changes.published) == ([], []))
        await fx_pagination().number(fx_actor, project.id, numbering)

        counted = [pages[1], pages[2], pages[4], pages[6]]
        labels = await _labels(fx_uow(), project.id, counted)
        expect(
            previewed
            == [NumberedPage(page_id=page.id, label=label) for page, label in zip(counted, labels, strict=True)]
        )
        expect([item.label for item in previewed] == ['x', 'xi', 'xii', 'xiii'])
        assert_expectations()

    async def test_a_numbering_asked_after_a_page_is_labelled_waits_and_numbers_the_result(
        self,
        fx_uow: UnitOfWorkFactory,
        fx_page_writes: PageWrites,
        fx_pagination_over: Callable[[UnitOfWork], PaginationService],
        fx_actor: Actor,
    ) -> None:
        """Verify a numbering asked behind the change of a page label waits for it, and then reads the label written.

        The book is held, the change of the label of the fifth page asks for it first and the numbering second. The
        numbering waits for the held book, so it cannot read the old label, and it numbers the page it finds
        afterwards.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_page_writes: Record of the pages the page repository writes.
        :type fx_page_writes: PageWrites
        :param fx_pagination_over: Function building the pagination service over a unit of work.
        :type fx_pagination_over: Callable[[UnitOfWork], PaginationService]
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project, pages = await _commit_book(fx_uow(), fx_actor, *LAYOUT, label=STALE_LABEL)
        numbering = PageNumbering(first_page_id=pages[2].id, last_page_id=pages[4].id, style=LabelStyle.ARABIC)
        fx_page_writes.start()

        def label_a_page(repos: BookRepositories, _project: Project) -> None:
            """Write a hand label of the fifth page.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _project: The project as the change finds it, which the work does not need.
            :type _project: Project
            """
            repos.pages.update(evolve(repos.pages.get(pages[4].id), manual_label=HAND_WRITTEN))

        async with anyio.create_task_group() as group, hold_book_in_thread(fx_uow(), project.id):
            group.start_soon(fx_uow().change_book, project.id, label_a_page)
            await anyio.wait_all_tasks_blocked()
            group.start_soon(fx_pagination_over(fx_uow()).number, fx_actor, project.id, numbering)
            await anyio.wait_all_tasks_blocked()
            expect(fx_page_writes.page_ids == set())

        # The numbering drops the hand label of the pages it counts, so it ran after the label was written
        stored = await fx_uow().read_book(project.id, lambda repos: repos.pages.get(pages[4].id))
        expect(stored.manual_label is None)
        assert_expectations()


class TestRecomputeInPageUseCases:
    """Tests for the labels that an insert, a delete, a move and an edit of pages recompute in their own transaction."""

    @staticmethod
    async def _numbered_book(
        pagination: Callable[[], PaginationService],
        uow: UnitOfWork,
        owner: Actor,
        count: int,
        **section: object,
    ) -> tuple[Project, list[Page]]:
        """Commit a book of text pages that one section numbers from the first page, in Arabic numerals.

        :param pagination: Function building the pagination service for one request.
        :type pagination: Callable[[], PaginationService]
        :param uow: Unit of work to commit through, over a database that stores the account of the owner.
        :type uow: UnitOfWork
        :param owner: Account owning the project.
        :type owner: Actor
        :param count: Number of pages.
        :type count: int
        :param section: Fields of the draft of the section other than its first page.
        :type section: object
        :returns: The project and its pages in book order, numbered.
        :rtype: tuple[Project, list[Page]]
        """
        project, pages = await _commit_book(uow, owner, *_text_book(count))
        await pagination().add(owner, project.id, _draft(pages[0], **section))
        return project, pages

    async def test_a_page_added_in_the_middle_renumbers_the_pages_after_it(
        self,
        fx_service: Callable[[], PageService],
        fx_pagination: Callable[[], PaginationService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        fx_book_changes: RecordingEventBus,
    ) -> None:
        """Verify a placeholder inserted before the third page takes its number, and the pages after it move on by one.

        :param fx_service: Function building the page service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_pagination: Function building the pagination service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the services act for.
        :type fx_actor: Actor
        :param fx_book_changes: Where the units of work of the test announce the changes of the books.
        :type fx_book_changes: RecordingEventBus
        """
        project, pages = await self._numbered_book(fx_pagination, fx_uow(), fx_actor, 4)
        fx_book_changes.published.clear()

        added = await fx_service().add(
            fx_actor,
            project.id,
            NewPage(
                origin=NewPageOrigin.PLACEHOLDER,
                kind=TEXT,
                anchor=PageAnchor(page_id=pages[2].id, side=Side.BEFORE),
            ),
        )

        expect(added.label == '3')
        expect(await _labels(fx_uow(), project.id, pages) == ['1', '2', '4', '5'])
        expect([BookResource.PAGES in parts for parts in fx_book_changes.changed_parts()] == [True])
        assert_expectations()

    async def test_a_page_added_with_a_label_keeps_it_and_still_counts(
        self,
        fx_service: Callable[[], PageService],
        fx_pagination: Callable[[], PaginationService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
    ) -> None:
        """Verify a new page that is labelled by hand is an exception, and the pages after it move on by one.

        :param fx_service: Function building the page service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_pagination: Function building the pagination service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the services act for.
        :type fx_actor: Actor
        """
        project, pages = await self._numbered_book(fx_pagination, fx_uow(), fx_actor, 3)

        [added] = await fx_service().add_many(
            fx_actor,
            project.id,
            [
                NewPage(
                    origin=NewPageOrigin.PLACEHOLDER,
                    kind=TEXT,
                    label=HAND_WRITTEN,
                    anchor=PageAnchor(page_id=pages[1].id, side=Side.AFTER),
                )
            ],
        )

        expect((added.label, added.page.manual_label) == (HAND_WRITTEN, HAND_WRITTEN))
        expect(await _labels(fx_uow(), project.id, pages) == ['1', '2', '4'])
        assert_expectations()

    async def test_deleting_a_page_renumbers_the_pages_after_it(
        self,
        fx_service: Callable[[], PageService],
        fx_pagination: Callable[[], PaginationService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        fx_book_changes: RecordingEventBus,
    ) -> None:
        """Verify the pages after a deleted page take the numbers it leaves free, and only the deletion is announced.

        :param fx_service: Function building the page service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_pagination: Function building the pagination service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the services act for.
        :type fx_actor: Actor
        :param fx_book_changes: Where the units of work of the test announce the changes of the books.
        :type fx_book_changes: RecordingEventBus
        """
        project, pages = await self._numbered_book(fx_pagination, fx_uow(), fx_actor, 4)
        fx_book_changes.published.clear()

        await fx_service().delete(fx_actor, project.id, pages[1].id)

        expect(await _labels(fx_uow(), project.id, [pages[0], pages[2], pages[3]]) == ['1', '2', '3'])
        expect([BookResource.PAGES in parts for parts in fx_book_changes.changed_parts()] == [True])
        assert_expectations()

    async def test_deleting_the_first_page_of_a_section_hands_the_section_to_the_next_page(
        self,
        fx_service: Callable[[], PageService],
        fx_pagination: Callable[[], PaginationService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
    ) -> None:
        """Verify the section that started at a deleted page starts at the page after it, and keeps numbering.

        :param fx_service: Function building the page service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_pagination: Function building the pagination service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the services act for.
        :type fx_actor: Actor
        """
        project, pages = await _commit_book(fx_uow(), fx_actor, *_text_book(5))
        await fx_pagination().add(fx_actor, project.id, _draft(pages[0], style=LabelStyle.ROMAN_LOWER))
        body = await fx_pagination().add(fx_actor, project.id, _draft(pages[2]))

        await fx_service().delete(fx_actor, project.id, pages[2].id)

        survivors = [pages[0], pages[1], pages[3], pages[4]]
        expect(await _labels(fx_uow(), project.id, survivors) == ['i', ROMAN_2, '1', '2'])
        sections = {section.id: section for section in await _sections(fx_uow(), project)}
        expect(sections[body.id].first_page_id == pages[3].id)
        assert_expectations()

    async def test_a_section_with_no_page_left_to_start_at_is_removed_with_the_last_page(
        self,
        fx_service: Callable[[], PageService],
        fx_pagination: Callable[[], PaginationService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
    ) -> None:
        """Verify deleting the last page removes the section that started there, and no other section.

        :param fx_service: Function building the page service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_pagination: Function building the pagination service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the services act for.
        :type fx_actor: Actor
        """
        project, pages = await _commit_book(fx_uow(), fx_actor, *_text_book(3))
        kept = await fx_pagination().add(fx_actor, project.id, _draft(pages[0]))
        await fx_pagination().add(fx_actor, project.id, _draft(pages[2], style=LabelStyle.ROMAN_LOWER))

        await fx_service().delete(fx_actor, project.id, pages[2].id)

        assert await _sections(fx_uow(), project) == [kept]

    async def test_a_section_handed_to_a_page_where_another_starts_gives_way_to_it(
        self,
        fx_service: Callable[[], PageService],
        fx_pagination: Callable[[], PaginationService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
    ) -> None:
        """Verify a section whose first page is deleted just before the start of the next section is dropped.

        :param fx_service: Function building the page service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_pagination: Function building the pagination service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the services act for.
        :type fx_actor: Actor
        """
        project, pages = await _commit_book(fx_uow(), fx_actor, *_text_book(4))
        await fx_pagination().add(fx_actor, project.id, _draft(pages[0], style=LabelStyle.ROMAN_LOWER))
        doomed = await fx_pagination().add(fx_actor, project.id, _draft(pages[2], start=FIRST_NUMBER))
        body = await fx_pagination().add(fx_actor, project.id, _draft(pages[3]))

        await fx_service().delete(fx_actor, project.id, pages[2].id)

        sections = {section.id: section for section in await _sections(fx_uow(), project)}
        expect(doomed.id not in sections)
        expect(sections[body.id].first_page_id == pages[3].id)
        expect(await _labels(fx_uow(), project.id, [pages[0], pages[1], pages[3]]) == ['i', ROMAN_2, '1'])
        assert_expectations()

    async def test_moving_a_page_renumbers_the_pages_by_their_new_places_and_keeps_labels_by_hand(
        self,
        fx_service: Callable[[], PageService],
        fx_pagination: Callable[[], PaginationService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        fx_book_changes: RecordingEventBus,
    ) -> None:
        """Verify a moved page takes the number of its new place, a label typed on another page stays, and one event follows.

        :param fx_service: Function building the page service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_pagination: Function building the pagination service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the services act for.
        :type fx_actor: Actor
        :param fx_book_changes: Where the units of work of the test announce the changes of the books.
        :type fx_book_changes: RecordingEventBus
        """
        project, pages = await _commit_book(fx_uow(), fx_actor, *_text_book(5))
        await fx_uow().change_book(
            project.id,
            lambda repos, _project: repos.pages.update(evolve(pages[1], manual_label=HAND_WRITTEN)),
        )
        await fx_pagination().add(fx_actor, project.id, _draft(pages[0]))
        fx_book_changes.published.clear()

        moved = await fx_service().move(
            fx_actor, project.id, pages[4].id, PageAnchor(page_id=pages[1].id, side=Side.BEFORE)
        )

        order = [pages[0], pages[4], pages[1], pages[2], pages[3]]
        expect(await _labels(fx_uow(), project.id, order) == ['1', '2', HAND_WRITTEN, '4', '5'])
        expect(moved.label == '2')
        expect([BookResource.PAGES in parts for parts in fx_book_changes.changed_parts()] == [True])
        assert_expectations()

    async def test_moving_a_group_of_pages_renumbers_the_book(
        self,
        fx_service: Callable[[], PageService],
        fx_pagination: Callable[[], PaginationService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
    ) -> None:
        """Verify a run of pages moved to the front of a numbered book is numbered by its new place.

        :param fx_service: Function building the page service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_pagination: Function building the pagination service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the services act for.
        :type fx_actor: Actor
        """
        project, pages = await self._numbered_book(fx_pagination, fx_uow(), fx_actor, 4)

        await fx_service().move_group(
            fx_actor, project.id, [pages[2].id, pages[3].id], PageAnchor(page_id=pages[0].id, side=Side.BEFORE)
        )

        # The section starts at the first page of the book once more, which is page 3 now, where it began at page 1
        assert await _labels(fx_uow(), project.id, [pages[2], pages[3], pages[0], pages[1]]) == ['', '', '1', '2']

    async def test_a_section_follows_its_first_page_when_the_page_moves(
        self,
        fx_service: Callable[[], PageService],
        fx_pagination: Callable[[], PaginationService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
    ) -> None:
        """Verify the page a section starts at carries the start of the section to its new place.

        :param fx_service: Function building the page service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_pagination: Function building the pagination service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the services act for.
        :type fx_actor: Actor
        """
        project, pages = await _commit_book(fx_uow(), fx_actor, *_text_book(4))
        await fx_pagination().add(fx_actor, project.id, _draft(pages[0], style=LabelStyle.ROMAN_LOWER))
        await fx_pagination().add(fx_actor, project.id, _draft(pages[2]))

        await fx_service().move(fx_actor, project.id, pages[2].id, PageAnchor(page_id=pages[3].id, side=Side.AFTER))

        assert await _labels(fx_uow(), project.id, [pages[0], pages[1], pages[3], pages[2]]) == [
            'i',
            ROMAN_2,
            ROMAN_3,
            '1',
        ]

    async def test_a_page_of_a_kind_a_series_takes_follows_the_series_once_its_kind_changes(
        self,
        fx_service: Callable[[], PageService],
        fx_pagination: Callable[[], PaginationService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
    ) -> None:
        """Verify a text page turned into a plate leaves the text count and takes the next number of the plates.

        :param fx_service: Function building the page service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_pagination: Function building the pagination service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the services act for.
        :type fx_actor: Actor
        """
        project, pages = await _commit_book(fx_uow(), fx_actor, *_text_book(4))
        await fx_pagination().add(fx_actor, project.id, _draft(pages[0]))
        await fx_pagination().add(fx_actor, project.id, _draft(pages[0], prefix=PLATE_PREFIX, kinds=PLATES))

        updated = await fx_service().update(fx_actor, project.id, pages[1].id, PageChanges(kind=PageKind.PLATE))

        expect(updated.label == FIRST_PLATE)
        expect(await _labels(fx_uow(), project.id, pages) == ['1', FIRST_PLATE, '2', '3'])
        assert_expectations()

    async def test_a_page_kept_out_of_the_book_takes_no_number(
        self,
        fx_service: Callable[[], PageService],
        fx_pagination: Callable[[], PaginationService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
    ) -> None:
        """Verify excluding a page empties its label and closes the gap, and bringing it back numbers it again.

        :param fx_service: Function building the page service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_pagination: Function building the pagination service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the services act for.
        :type fx_actor: Actor
        """
        project, pages = await self._numbered_book(fx_pagination, fx_uow(), fx_actor, 3)

        await fx_service().update(fx_actor, project.id, pages[1].id, PageChanges(included=False))
        expect(await _labels(fx_uow(), project.id, pages) == ['1', '', '2'])
        await fx_service().update(fx_actor, project.id, pages[1].id, PageChanges(included=True))

        expect(await _labels(fx_uow(), project.id, pages) == ['1', '2', '3'])
        assert_expectations()

    async def test_a_typed_label_is_an_exception_and_clearing_it_gives_the_page_back_to_its_section(
        self,
        fx_service: Callable[[], PageService],
        fx_pagination: Callable[[], PaginationService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
    ) -> None:
        """Verify a label typed over a page stays through a renumbering, and clearing it restores the computed number.

        :param fx_service: Function building the page service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_pagination: Function building the pagination service for one request.
        :type fx_pagination: Callable[[], PaginationService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the services act for.
        :type fx_actor: Actor
        """
        project, pages = await self._numbered_book(fx_pagination, fx_uow(), fx_actor, 3)

        typed = await fx_service().update(fx_actor, project.id, pages[1].id, PageChanges(label=HAND_WRITTEN))
        await fx_service().delete(fx_actor, project.id, pages[0].id)
        expect((typed.label, typed.page.manual_label) == (HAND_WRITTEN, HAND_WRITTEN))
        expect(await _labels(fx_uow(), project.id, [pages[1], pages[2]]) == [HAND_WRITTEN, '2'])
        cleared = await fx_service().update(fx_actor, project.id, pages[1].id, PageChanges(label=''))

        expect((cleared.label, cleared.page.manual_label) == ('1', None))
        expect(await _labels(fx_uow(), project.id, [pages[1], pages[2]]) == ['1', '2'])
        assert_expectations()

    async def test_a_book_without_sections_keeps_every_label_it_has(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_page_writes: PageWrites,
        fx_actor: Actor,
    ) -> None:
        """Verify moves and deletions leave the labels of a book that has no section alone, and write only what they change.

        :param fx_service: Function building the page service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_page_writes: Record of the pages the page repository writes.
        :type fx_page_writes: PageWrites
        :param fx_actor: Account the services act for.
        :type fx_actor: Actor
        """
        project, pages = await _commit_book(fx_uow(), fx_actor, *_text_book(3), label=STALE_LABEL)
        fx_page_writes.start()

        await fx_service().move(fx_actor, project.id, pages[2].id, PageAnchor(page_id=pages[0].id, side=Side.BEFORE))
        await fx_service().delete(fx_actor, project.id, pages[1].id)

        expect(await _labels(fx_uow(), project.id, [pages[0], pages[2]]) == [STALE_LABEL] * 2)
        expect(fx_page_writes.page_ids == {pages[2].id})
        assert_expectations()
