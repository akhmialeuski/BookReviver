"""Tests for the page use cases, against in-memory persistence."""

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.ids import PageId
from bookreviver.domain.values import SliceRequest
from bookreviver.services.pages import PageService
from tests.helpers.builders import make_page, make_page_version, make_project, make_scan, make_source, new_account_id
from tests.helpers.seeding import commit_project

if TYPE_CHECKING:
    from collections.abc import Callable

    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.domain.entities import Actor, Page, PageVersion, Project

pytestmark = pytest.mark.anyio

# Stored against the book order, and sorting as a0, a0V, a1 in bytes
STORED_KEYS: list[str] = ['a1', 'a0', 'a0V']
BOOK_ORDER: list[int] = [1, 2, 0]
WINDOW: SliceRequest = SliceRequest(offset=1, limit=1)


@pytest.fixture
def fx_service(fx_database: InMemoryDatabase) -> Callable[[], PageService]:
    """Return a function building the service for one request.

    :param fx_database: In-memory database every request of the test shares.
    :type fx_database: InMemoryDatabase
    :returns: Function building a service over a new unit of work.
    :rtype: Callable[[], PageService]
    """
    return lambda: PageService(uow=InMemoryUnitOfWork(fx_database))


async def _commit_book(database: InMemoryDatabase, project: Project) -> tuple[list[Page], list[PageVersion]]:
    """Commit a book of three pages stored against the book order, the middle one in the book a placeholder.

    :param database: In-memory database to commit into.
    :type database: InMemoryDatabase
    :param project: Project of the book.
    :type project: Project
    :returns: The pages in the order they were stored, and the base versions of the two that have one.
    :rtype: tuple[list[Page], list[PageVersion]]
    """
    source = make_source(project_id=project.id)
    scans = [make_scan(source=source, number=number) for number in range(2)]
    shown = [scans[0], None, scans[1]]
    pages = [make_page(project_id=project.id, order_key=key, scan=scan) for key, scan in zip(STORED_KEYS, shown)]
    versions = [make_page_version(page_id=pages[index].id) for index in (0, 2)]
    await commit_project(database, project, *pages, sources=[source], scans=scans, versions=versions)
    return pages, versions


class TestManifest:
    """Tests for PageService.manifest()."""

    async def test_lists_the_pages_in_book_order_with_their_positions_and_versions(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify pages come in the byte order of their keys, numbered from zero, each with its own base version.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=fx_actor.account_id)
        pages, versions = await _commit_book(fx_database, project)

        manifest = await fx_service().manifest(fx_actor, project.id, SliceRequest())

        expect([overview.page for overview in manifest.items] == [pages[index] for index in BOOK_ORDER])
        expect([overview.position for overview in manifest.items] == [0, 1, 2])
        expect([overview.base_version for overview in manifest.items] == [None, versions[1], versions[0]])
        expect(manifest.total == len(pages))
        assert_expectations()

    async def test_a_window_continues_the_positions_of_the_book(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify a window starting at an offset numbers its pages from that offset and reports the whole book.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=fx_actor.account_id)
        pages, _ = await _commit_book(fx_database, project)

        manifest = await fx_service().manifest(fx_actor, project.id, WINDOW)

        expect([(overview.page, overview.position) for overview in manifest.items] == [(pages[2], 1)])
        expect(manifest.total == len(pages))
        assert_expectations()

    async def test_shows_the_newest_base_version_of_a_page(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify a page cut from its scan again shows the base version created last.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=fx_actor.account_id)
        page = make_page(project_id=project.id)
        old, new = make_page_version(page_id=page.id, minutes=1), make_page_version(page_id=page.id, minutes=2)
        await commit_project(fx_database, project, page, versions=[new, old])

        manifest = await fx_service().manifest(fx_actor, project.id, SliceRequest())

        assert [overview.base_version for overview in manifest.items] == [new]

    async def test_another_accounts_project_is_not_found(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify the pages of another account's project cannot be listed.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=new_account_id())
        await _commit_book(fx_database, project)

        with pytest.raises(NotFoundError):
            await fx_service().manifest(fx_actor, project.id, SliceRequest())


class TestGet:
    """Tests for PageService.get()."""

    async def test_returns_the_page_with_its_position_and_base_version(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify a page read by its identifier knows its place in the book and its base version.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=fx_actor.account_id)
        pages, versions = await _commit_book(fx_database, project)

        overview = await fx_service().get(fx_actor, project.id, pages[0].id)

        expect(overview.page == pages[0])
        expect(overview.position == len(pages) - 1)
        expect(overview.base_version == versions[0])
        assert_expectations()

    @pytest.mark.parametrize('owned', [False, True], ids=['another-accounts-project', 'page-of-another-project'])
    async def test_page_outside_the_project_is_not_found(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor, owned: bool
    ) -> None:
        """Verify a page is found only through the project that holds it, and only for the project's owner.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param owned: Whether the actor owns the project the request names, the page being in a project of theirs.
        :type owned: bool
        """
        project = make_project(owner_id=fx_actor.account_id)
        holder = make_project(owner_id=fx_actor.account_id if owned else new_account_id())
        page = make_page(project_id=holder.id)
        await commit_project(fx_database, project)
        await commit_project(fx_database, holder, page)

        with pytest.raises(NotFoundError):
            await fx_service().get(fx_actor, project.id if owned else holder.id, page.id)

    async def test_missing_page_is_not_found(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify an identifier no page has is reported as not found.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=fx_actor.account_id)
        await commit_project(fx_database, project)

        with pytest.raises(NotFoundError):
            await fx_service().get(fx_actor, project.id, PageId(uuid4()))
