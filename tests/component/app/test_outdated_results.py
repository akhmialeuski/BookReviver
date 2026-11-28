"""Tests for the opening of a book: the results a replaced version of a processor made are marked stale then.

The application starts without opening a book, so the outdated results of a book are found when a request first opens
the book, whether the user opens it or the library shows it, and the record of the book keeps the processors it was
checked against, so a book checked against the processors installed is not checked again.
"""

from typing import TYPE_CHECKING, NamedTuple

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect
from fastapi import status

from bookreviver.core.plugins import ProcessorCatalog
from bookreviver.domain.images import Renditions
from bookreviver.domain.stages import PageStageKey, Stage, StageState
from bookreviver.domain.versions import VersionState
from tests.harness.builders.domain import (
    make_page,
    make_page_stage,
    make_page_version,
    make_record,
    make_scan,
    make_source,
    new_book_id,
)
from tests.harness.builders.processing import ProcessingFakesProvider
from tests.harness.builders.seeding import store_book
from tests.harness.fakes.jobs import JobFakesProvider
from tests.harness.fakes.processors import FakeProcessor

if TYPE_CHECKING:
    from collections.abc import Sequence

    import httpx
    from dishka import AsyncContainer, Provider

    from bookreviver.domain.accounts import Actor
    from bookreviver.domain.books import BookRecord
    from bookreviver.domain.ids import BookId
    from bookreviver.domain.pages import Page
    from bookreviver.domain.sources import Scan, Source
    from bookreviver.domain.stages import PageStage
    from bookreviver.domain.versions import PageVersion, ProcessorRef
    from bookreviver.ports.persistence import BookRepositories
    from tests.harness.adapters.database import BookDatabasesFactory, CatalogueDatabaseFactory
    from tests.harness.fakes.jobs import JobFakes

pytestmark = pytest.mark.anyio

BOOKS_PATH: str = '/api/v1/books'
BOOK_PATH: str = '/api/v1/books/{book_id}'
# A version of the geometry processor that is older than the one of the catalogue of the fakes, which has the version 1
OUTDATED_GEOMETRY = evolve(FakeProcessor.spec.ref, version='0')


class Book(NamedTuple):
    """A book of one page whose geometry result was made by an older version of its processor.

    :ivar book_id: The identifier the book is stored under.
    :ivar book_record: The record of the book.
    :ivar source: The source the page was cut from.
    :ivar scan: The scan of the source the page shows.
    :ivar page: Its only page.
    :ivar versions: The base version of the page and the outdated geometry version built on it.
    :ivar records: The fresh records of the page split, with the base version, and of the geometry, with the other.
    """

    book_id: BookId
    book_record: BookRecord
    source: Source
    scan: Scan
    page: Page
    versions: tuple[PageVersion, PageVersion]
    records: tuple[PageStage, PageStage]


@pytest.fixture
def fx_book() -> Book:
    """Build a book whose page has a fresh geometry record over an outdated version.

    :returns: The book, not stored yet.
    :rtype: Book
    """
    book_record = make_record()
    source = make_source()
    scan = evolve(make_scan(source=source, number=0), renditions=Renditions(ready=True))
    page = make_page(scan=scan)
    base = evolve(make_page_version(page_id=page.id), state=VersionState.READY)
    geometry = evolve(
        make_page_version(page_id=page.id, minutes=1),
        stage=Stage.GEOMETRY,
        processor=OUTDATED_GEOMETRY,
        input_id=base.id,
        state=VersionState.READY,
    )
    return Book(
        book_id=new_book_id(),
        book_record=book_record,
        source=source,
        scan=scan,
        page=page,
        versions=(base, geometry),
        records=(
            make_page_stage(page_id=page.id, stage=Stage.PAGE_SPLIT, head_version_id=base.id),
            make_page_stage(page_id=page.id, stage=Stage.GEOMETRY, head_version_id=geometry.id),
        ),
    )


@pytest.fixture
async def fx_extra_providers(
    fx_fakes: JobFakes,
    fx_catalogue: CatalogueDatabaseFactory,
    fx_book: Book,
    fx_actor: Actor,
    fx_databases: BookDatabasesFactory,
) -> Sequence[Provider]:
    """Store the book before the application starts, and give the application the fakes and the fake catalogue.

    The changes that store the book are not recorded as published, so the events the test sees are the application's.

    :param fx_fakes: Adapters of the test.
    :type fx_fakes: JobFakes
    :param fx_catalogue: Function opening a new catalogue database over the database of the test.
    :type fx_catalogue: CatalogueDatabaseFactory
    :param fx_book: The book to store.
    :type fx_book: Book
    :param fx_actor: Account that owns the book.
    :type fx_actor: Actor
    :param fx_databases: Function opening a new opener of the databases of the books of the test.
    :type fx_databases: BookDatabasesFactory
    :returns: The providers of the fakes and of the fake processors.
    :rtype: Sequence[Provider]
    """
    await store_book(
        fx_catalogue(),
        fx_book.book_record,
        fx_book.page,
        owner=fx_actor.account_id,
        book_id=fx_book.book_id,
        sources=[fx_book.source],
        scans=[fx_book.scan],
        versions=fx_book.versions,
        databases=fx_databases(),
    )

    def save_records(repos: BookRepositories, _book_record: BookRecord) -> None:
        """Save the fresh records of the book.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :param _book_record: Record of the book, which the work does not need.
        :type _book_record: BookRecord
        """
        for record in fx_book.records:
            repos.page_stages.save(record)

    await fx_databases().open(fx_book.book_id).change(save_records)
    fx_fakes.events.published.clear()
    return (JobFakesProvider(fx_fakes), ProcessingFakesProvider())


async def states(databases: BookDatabasesFactory, book: Book) -> list[StageState]:
    """Read the states of the records of the page split and of the geometry of the page of the book.

    :param databases: Function opening a new opener of the databases of the books of the test.
    :type databases: BookDatabasesFactory
    :param book: The stored book.
    :type book: Book
    :returns: The state of the page split, then of the geometry.
    :rtype: list[StageState]
    """
    return (
        await databases()
        .open(book.book_id)
        .read(
            lambda repos: [
                repos.page_stages.get(PageStageKey(book.page.id, stage)).state
                for stage in (Stage.PAGE_SPLIT, Stage.GEOMETRY)
            ],
        )
    )


async def installed(container: AsyncContainer) -> frozenset[ProcessorRef]:
    """Name the processors the application has installed, by key and version.

    :param container: Container of the running application.
    :type container: AsyncContainer
    :returns: The processors of its catalogue.
    :rtype: frozenset[ProcessorRef]
    """
    return frozenset(spec.ref for spec in (await container.get(ProcessorCatalog)).specs())


class TestOpening:
    """Tests for the check of the results of a book against the processors installed, when the book is first opened."""

    @pytest.mark.usefixtures('fx_app')
    async def test_the_start_alone_leaves_the_book_as_it_is(
        self, fx_fakes: JobFakes, fx_book: Book, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the outdated record stays fresh and nothing is announced while no request opens the book.

        :param fx_fakes: Adapters of the test, whose event bus records what is announced.
        :type fx_fakes: JobFakes
        :param fx_book: The stored book.
        :type fx_book: Book
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        assert (await states(fx_databases, fx_book), fx_fakes.events.published) == (
            [StageState.FRESH, StageState.FRESH],
            [],
        )

    @pytest.mark.parametrize('path', [pytest.param(BOOK_PATH, id='the-book'), pytest.param(BOOKS_PATH, id='library')])
    async def test_the_first_opening_marks_a_fresh_stage_of_an_outdated_processor_stale(
        self,
        fx_client: httpx.AsyncClient,
        fx_container: AsyncContainer,
        fx_book: Book,
        fx_databases: BookDatabasesFactory,
        path: str,
    ) -> None:
        """Verify the geometry record is stale once the book is opened or shown, and the base version's is not.

        The record of the book is given the processors installed, and what the request answered was read after the
        check.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_container: Container of the running application.
        :type fx_container: AsyncContainer
        :param fx_book: The stored book.
        :type fx_book: Book
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        :param path: The address the request reads, of the book or of the library.
        :type path: str
        """
        response = await fx_client.get(path.format(book_id=fx_book.book_id))
        checked = await fx_databases().open(fx_book.book_id).read(lambda repos: repos.book_record.get())

        expect(response.status_code == status.HTTP_200_OK)
        expect(await states(fx_databases, fx_book) == [StageState.FRESH, StageState.STALE])
        expect(checked.checked_processors == await installed(fx_container))
        assert_expectations()

    async def test_a_book_checked_against_the_processors_installed_is_not_checked_again(
        self,
        fx_client: httpx.AsyncClient,
        fx_container: AsyncContainer,
        fx_book: Book,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify the outdated record stays fresh when the record says the book was checked with these processors.

        The record stands for a book an earlier process checked, so this one reads the record and goes no further.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_container: Container of the running application.
        :type fx_container: AsyncContainer
        :param fx_book: The stored book.
        :type fx_book: Book
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        processors = await installed(fx_container)
        await (
            fx_databases()
            .open(fx_book.book_id)
            .change(
                lambda repos, book_record: repos.book_record.update(evolve(book_record, checked_processors=processors))
            )
        )

        response = await fx_client.get(BOOK_PATH.format(book_id=fx_book.book_id))

        expect(response.status_code == status.HTTP_200_OK)
        expect(await states(fx_databases, fx_book) == [StageState.FRESH, StageState.FRESH])
        assert_expectations()
