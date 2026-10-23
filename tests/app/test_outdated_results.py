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
)
from tests.helpers.fakes_jobs import JobFakes, JobFakesProvider
from tests.helpers.processing import ProcessingFakesProvider
from tests.helpers.processors import FakeProcessor
from tests.helpers.seeding import store_book

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dishka import Provider

    from bookreviver.domain.entities import Actor, BookRecord, Page, PageStage, PageVersion, Scan, Source
    from bookreviver.ports.persistence import BookRepositories
    from tests.helpers.database import UnitOfWorkFactory

pytestmark = pytest.mark.anyio

# A version of the geometry processor that is older than the one of the catalogue of the fakes, which has the version 1
OUTDATED_GEOMETRY = evolve(FakeProcessor.spec.ref, version='0')


class Book(NamedTuple):
    """A book of one page whose geometry result was made by an older version of its processor.

    :ivar book_record: The record of the book.
    :ivar source: The source the page was cut from.
    :ivar scan: The scan of the source the page shows.
    :ivar page: Its only page.
    :ivar versions: The base version of the page and the outdated geometry version built on it.
    :ivar records: The fresh records of the page split, with the base version, and of the geometry, with the other.
    """

    book_record: BookRecord
    source: Source
    scan: Scan
    page: Page
    versions: tuple[PageVersion, PageVersion]
    records: tuple[PageStage, PageStage]


@pytest.fixture
def fx_fakes() -> JobFakes:
    """Build the adapters the application runs on besides its database.

    :returns: Fakes with nothing published.
    :rtype: JobFakes
    """
    return JobFakes()


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
    fx_fakes: JobFakes, fx_uow: UnitOfWorkFactory, fx_book: Book, fx_actor: Actor
) -> Sequence[Provider]:
    """Store the book before the application starts, and give the application the fakes and the fake catalogue.

    :param fx_fakes: Adapters of the test.
    :type fx_fakes: JobFakes
    :param fx_uow: Function opening a new unit of work over the database of the test.
    :type fx_uow: UnitOfWorkFactory
    :param fx_book: The book to store.
    :type fx_book: Book
    :param fx_actor: Account that owns the book.
    :type fx_actor: Actor
    :returns: The providers of the fakes and of the fake processors.
    :rtype: Sequence[Provider]
    """
    await store_book(
        fx_uow(),
        fx_book.book_record,
        fx_book.page,
        owner=fx_actor.account_id,
        sources=[fx_book.source],
        scans=[fx_book.scan],
        versions=fx_book.versions,
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

    await fx_uow().change_book(fx_book.book_record.id, save_records)
    return (JobFakesProvider(fx_fakes), ProcessingFakesProvider())


class TestStart:
    """Tests for what the lifespan of the application does before it serves a request."""

    @pytest.mark.usefixtures('fx_app')
    async def test_marks_a_fresh_stage_of_an_outdated_processor_stale_before_the_first_request(
        self, fx_fakes: JobFakes, fx_uow: UnitOfWorkFactory, fx_book: Book
    ) -> None:
        """Verify the geometry record is stale once the application runs, and the record of the base version is not.

        The running application is only started, and no request is made, so the mark can only come from the start
        itself. No service announces it, and the unit of work announces the change of the stages of the book.

        :param fx_fakes: Adapters of the test, whose event bus records what is announced.
        :type fx_fakes: JobFakes
        :param fx_uow: Function opening a new unit of work over the database of the test, which holds the book.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book: The stored book.
        :type fx_book: Book
        """
        states = await fx_uow().read_book(
            fx_book.book_record.id,
            lambda repos: [
                repos.page_stages.get(PageStageKey(fx_book.page.id, stage)).state
                for stage in (Stage.PAGE_SPLIT, Stage.GEOMETRY)
            ],
        )

        announced = [event for event in fx_fakes.events.published if not isinstance(event, BookChanged)]
        changes = [event.resources for event in fx_fakes.events.published if isinstance(event, BookChanged)]
        assert (states, announced, changes) == (
            [StageState.FRESH, StageState.STALE],
            [],
            [frozenset({BookResource.STAGES})],
        )
