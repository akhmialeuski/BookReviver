"""Tests for the start of the application: the results a replaced version of a processor made are marked stale."""

from typing import TYPE_CHECKING, NamedTuple

import pytest
from attrs import evolve

from bookreviver.domain.enums import BookResource, Stage, StageState, VersionState
from bookreviver.domain.events import BookChanged
from bookreviver.domain.values import PageStageKey, Renditions
from tests.helpers.builders import (
    make_page,
    make_page_stage,
    make_page_version,
    make_record,
    make_scan,
    make_source,
    new_book_id,
)
from tests.helpers.fakes_jobs import JobFakes, JobFakesProvider
from tests.helpers.processing import ProcessingFakesProvider
from tests.helpers.processors import FakeProcessor
from tests.helpers.seeding import store_book

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dishka import Provider

    from bookreviver.domain.entities import Actor, BookRecord, Page, PageStage, PageVersion, Scan, Source
    from bookreviver.domain.ids import BookId
    from bookreviver.ports.persistence import BookRepositories
    from tests.helpers.database import BookDatabasesFactory, CatalogueDatabaseFactory
    from tests.helpers.fakes_jobs import RecordingEventBus

pytestmark = pytest.mark.anyio

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
def fx_fakes(fx_book_parts: RecordingEventBus) -> JobFakes:
    """Build the adapters the application runs on besides its database.

    :param fx_book_parts: The recording bus registered as the event channel of every book of the test.
    :type fx_book_parts: RecordingEventBus
    :returns: Fakes with nothing published.
    :rtype: JobFakes
    """
    return JobFakes(events=fx_book_parts)


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
    return (JobFakesProvider(fx_fakes), ProcessingFakesProvider())


class TestStart:
    """Tests for what the lifespan of the application does before it serves a request."""

    @pytest.mark.usefixtures('fx_app')
    async def test_marks_a_fresh_stage_of_an_outdated_processor_stale_before_the_first_request(
        self, fx_fakes: JobFakes, fx_book: Book, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the geometry record is stale once the application runs, and the record of the base version is not.

        The running application is only started, and no request is made, so the mark can only come from the start
        itself. No service announces it, and the commit of the change announces the stages of the book.

        :param fx_fakes: Adapters of the test, whose event bus records what is announced.
        :type fx_fakes: JobFakes
        :param fx_book: The stored book.
        :type fx_book: Book
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        states = (
            await fx_databases()
            .open(fx_book.book_id)
            .read(
                lambda repos: [
                    repos.page_stages.get(PageStageKey(fx_book.page.id, stage)).state
                    for stage in (Stage.PAGE_SPLIT, Stage.GEOMETRY)
                ],
            )
        )

        announced = [event for event in fx_fakes.events.published if not isinstance(event, BookChanged)]
        changes = [event.resources for event in fx_fakes.events.published if isinstance(event, BookChanged)]
        assert (states, announced, changes) == (
            [StageState.FRESH, StageState.STALE],
            [],
            [frozenset({BookResource.STAGES})],
        )
