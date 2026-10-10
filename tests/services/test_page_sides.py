"""Tests for the stages that go stale when a change of the places of the pages turns a page over to the other side."""

from typing import TYPE_CHECKING, NamedTuple

import pytest
from attrs import evolve

from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.domain.enums import NewPageOrigin, PageKind, Side, Stage, StageState, ValueScope
from bookreviver.domain.events import PageStageChanged
from bookreviver.domain.values import NewPage, PageAnchor
from tests.helpers.builders import make_page, make_page_stage, make_project, make_step_values
from tests.helpers.seeding import commit_project

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.domain.entities import Actor, Page, Project
    from bookreviver.services.pages import PageService
    from tests.helpers.fakes_jobs import RecordingEventBus

pytestmark = pytest.mark.anyio

PAGE_COUNT: int = 4
CHECKED_STAGES: tuple[Stage, ...] = (Stage.GEOMETRY, Stage.CLEANUP)
GROUP: str = 'Index'


class Book(NamedTuple):
    """A book of four pages that all went through the geometry and the cleanup stage.

    :ivar project: The project of the book.
    :ivar pages: The pages in book order.
    """

    project: Project
    pages: list[Page]


class Reordering(NamedTuple):
    """A change of the places of the pages, and the pages of the four-page book it turns over to the other side.

    :ivar act: The use case that changes the places.
    :ivar turned: Indexes of the pages whose place changed by an odd number.
    """

    act: Callable[[PageService, Actor, Book], Awaitable[None]]
    turned: set[int]


async def _move(service: PageService, actor: Actor, book: Book) -> None:
    """Put the first page after the third, so the second and the third move up by one place.

    :param service: The page service.
    :type service: PageService
    :param actor: Owner of the book.
    :type actor: Actor
    :param book: The book.
    :type book: Book
    """
    await service.move(actor, book.project.id, book.pages[0].id, PageAnchor(page_id=book.pages[2].id, side=Side.AFTER))


async def _move_group(service: PageService, actor: Actor, book: Book) -> None:
    """Put the second and the third page after the fourth, so they move down by one place.

    :param service: The page service.
    :type service: PageService
    :param actor: Owner of the book.
    :type actor: Actor
    :param book: The book.
    :type book: Book
    """
    anchor = PageAnchor(page_id=book.pages[3].id, side=Side.AFTER)
    await service.move_group(actor, book.project.id, [book.pages[1].id, book.pages[2].id], anchor)


async def _delete(service: PageService, actor: Actor, book: Book) -> None:
    """Delete the first page, so every page after it moves up by one place.

    :param service: The page service.
    :type service: PageService
    :param actor: Owner of the book.
    :type actor: Actor
    :param book: The book.
    :type book: Book
    """
    await service.delete(actor, book.project.id, book.pages[0].id)


async def _insert(service: PageService, actor: Actor, book: Book) -> None:
    """Add a placeholder before the second page, so every page from it on moves down by one place.

    :param service: The page service.
    :type service: PageService
    :param actor: Owner of the book.
    :type actor: Actor
    :param book: The book.
    :type book: Book
    """
    new_page = NewPage(
        origin=NewPageOrigin.PLACEHOLDER,
        kind=PageKind.TEXT,
        anchor=PageAnchor(page_id=book.pages[1].id, side=Side.BEFORE),
    )
    await service.add(actor, book.project.id, new_page)


REORDERINGS: dict[str, Reordering] = {
    'move': Reordering(_move, {1, 2}),
    'move-group': Reordering(_move_group, {1, 2}),
    'delete': Reordering(_delete, {1, 2, 3}),
    'insert': Reordering(_insert, {1, 2, 3}),
}


async def _commit_book(database: InMemoryDatabase, actor: Actor, valued: tuple[ValueScope, Stage] | None) -> Book:
    """Commit a book of four pages with a fresh record of each checked stage, and one value for a step of a stage.

    :param database: In-memory database to commit into.
    :type database: InMemoryDatabase
    :param actor: Owner of the book.
    :type actor: Actor
    :param valued: The part of the pages and the stage of the one value the book has for a step, or None for no value.
    :type valued: tuple[ValueScope, Stage] | None
    :returns: The book.
    :rtype: Book
    """
    project = make_project(owner_id=actor.account_id)
    pages = [make_page(project_id=project.id, order_key=f'a{number}') for number in range(PAGE_COUNT)]
    await commit_project(database, project, *pages)
    uow = InMemoryUnitOfWork(database)
    async with uow.change_book(project.id):
        for page in pages:
            for stage in CHECKED_STAGES:
                await uow.page_stages.save(make_page_stage(page_id=page.id, stage=stage))
        if valued is not None:
            scope, stage = valued
            group_label = GROUP if scope is ValueScope.GROUP else ''
            values = make_step_values(project_id=project.id, scope=scope, group_label=group_label)
            await uow.step_values.save(evolve(values, stage=stage))
    return Book(project=project, pages=pages)


async def _stale(database: InMemoryDatabase, book: Book) -> set[tuple[int, Stage]]:
    """Read which stages of which pages are stale.

    :param database: In-memory database of the test.
    :type database: InMemoryDatabase
    :param book: The book.
    :type book: Book
    :returns: The index of the page in the original book order and the stage, of each stale record.
    :rtype: set[tuple[int, Stage]]
    """
    index = {page.id: place for place, page in enumerate(book.pages)}
    records = await InMemoryUnitOfWork(database).page_stages.list_for_pages(list(index))
    return {(index[record.page_id], record.stage) for record in records if record.state is StageState.STALE}


class TestPagesTurnedOver:
    """Tests for the pages whose odd or even side changes when pages are moved, added or deleted."""

    @pytest.mark.parametrize('reordering', REORDERINGS.values(), ids=REORDERINGS.keys())
    async def test_the_pages_that_turned_over_go_stale_where_the_odd_pages_have_a_value(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_actor: Actor,
        reordering: Reordering,
    ) -> None:
        """Verify a page whose place changed by an odd number goes stale in the stage of the value, and no other does.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param reordering: The change of the places and the pages it turns over.
        :type reordering: Reordering
        """
        book = await _commit_book(fx_database, fx_actor, (ValueScope.ODD, Stage.GEOMETRY))
        await reordering.act(fx_service(), fx_actor, book)
        assert await _stale(fx_database, book) == {(place, Stage.GEOMETRY) for place in reordering.turned}

    async def test_only_the_stages_that_have_a_value_for_a_side_go_stale(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify the stage the even pages have a value in is marked, and the stage that has none is left fresh.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        book = await _commit_book(fx_database, fx_actor, (ValueScope.EVEN, Stage.CLEANUP))
        await _move(fx_service(), fx_actor, book)
        assert await _stale(fx_database, book) == {(1, Stage.CLEANUP), (2, Stage.CLEANUP)}

    @pytest.mark.parametrize('valued', [None, (ValueScope.GROUP, Stage.GEOMETRY)], ids=['no-value', 'group-value'])
    async def test_a_book_with_no_value_for_a_side_marks_nothing(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_actor: Actor,
        valued: tuple[ValueScope, Stage] | None,
    ) -> None:
        """Verify moving a page leaves every stage fresh when no value is for the odd or the even pages.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param valued: The one value the book has, or None.
        :type valued: tuple[ValueScope, Stage] | None
        """
        book = await _commit_book(fx_database, fx_actor, valued)
        await _move(fx_service(), fx_actor, book)
        assert await _stale(fx_database, book) == set()

    async def test_the_marked_stages_are_announced_after_the_commit(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_actor: Actor,
        fx_events: RecordingEventBus,
    ) -> None:
        """Verify one event is published for each stage record a move marked stale.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_events: Recording event bus the service publishes to.
        :type fx_events: RecordingEventBus
        """
        book = await _commit_book(fx_database, fx_actor, (ValueScope.ODD, Stage.GEOMETRY))
        await _move(fx_service(), fx_actor, book)
        announced = {event.stage.page_id for event in fx_events.published if isinstance(event, PageStageChanged)}
        assert announced == {book.pages[1].id, book.pages[2].id}
