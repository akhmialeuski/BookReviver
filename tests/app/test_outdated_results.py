"""Tests for the start of the application: the results a replaced version of a processor made are marked stale."""

from typing import TYPE_CHECKING, NamedTuple

import pytest
from attrs import evolve

from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.domain.enums import Stage, StageState, VersionState
from bookreviver.domain.values import PageStageKey, Renditions
from tests.helpers.builders import (
    make_page,
    make_page_stage,
    make_page_version,
    make_project,
    make_scan,
    make_source,
    new_account_id,
)
from tests.helpers.fakes_jobs import JobFakes, JobFakesProvider
from tests.helpers.processing import ProcessingFakesProvider
from tests.helpers.processors import FakeProcessor
from tests.helpers.seeding import commit_project

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dishka import Provider

    from bookreviver.domain.entities import Page, PageStage, PageVersion, Project, Scan, Source

pytestmark = pytest.mark.anyio

# A version of the geometry processor that is older than the one of the catalogue of the fakes, which has the version 1
OUTDATED_GEOMETRY = evolve(FakeProcessor.spec.ref, version='0')


class Book(NamedTuple):
    """A book of one page whose geometry result was made by an older version of its processor.

    :ivar project: The project of the book.
    :ivar source: The source the page was cut from.
    :ivar scan: The scan of the source the page shows.
    :ivar page: Its only page.
    :ivar versions: The base version of the page and the outdated geometry version built on it.
    :ivar records: The fresh records of the page split, with the base version, and of the geometry, with the other.
    """

    project: Project
    source: Source
    scan: Scan
    page: Page
    versions: tuple[PageVersion, PageVersion]
    records: tuple[PageStage, PageStage]


@pytest.fixture
def fx_fakes() -> JobFakes:
    """Build the adapters the application runs on, with an empty database.

    :returns: Fakes with an empty database and nothing published.
    :rtype: JobFakes
    """
    return JobFakes()


@pytest.fixture
def fx_book() -> Book:
    """Build a book whose page has a fresh geometry record over an outdated version.

    :returns: The book, not stored yet.
    :rtype: Book
    """
    project = make_project(owner_id=new_account_id())
    source = make_source(project_id=project.id)
    scan = evolve(make_scan(source=source, number=0), renditions=Renditions(ready=True))
    page = make_page(project_id=project.id, scan=scan)
    base = evolve(make_page_version(page_id=page.id), state=VersionState.READY)
    geometry = evolve(
        make_page_version(page_id=page.id, minutes=1),
        stage=Stage.GEOMETRY,
        processor=OUTDATED_GEOMETRY,
        input_id=base.id,
        state=VersionState.READY,
    )
    return Book(
        project=project,
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
async def fx_extra_providers(fx_fakes: JobFakes, fx_book: Book) -> Sequence[Provider]:
    """Store the book before the application starts, and give the application the fakes and the fake catalogue.

    :param fx_fakes: Adapters of the test, whose database the application reads.
    :type fx_fakes: JobFakes
    :param fx_book: The book to store.
    :type fx_book: Book
    :returns: The providers of the fakes and of the fake processors.
    :rtype: Sequence[Provider]
    """
    await commit_project(
        fx_fakes.database,
        fx_book.project,
        fx_book.page,
        sources=[fx_book.source],
        scans=[fx_book.scan],
        versions=fx_book.versions,
    )
    uow = InMemoryUnitOfWork(fx_fakes.database)
    for record in fx_book.records:
        await uow.page_stages.save(record)
    await uow.commit()
    return (JobFakesProvider(fx_fakes), ProcessingFakesProvider())


class TestStart:
    """Tests for what the lifespan of the application does before it serves a request."""

    @pytest.mark.usefixtures('fx_app')
    async def test_marks_a_fresh_stage_of_an_outdated_processor_stale_before_the_first_request(
        self, fx_fakes: JobFakes, fx_book: Book
    ) -> None:
        """Verify the geometry record is stale once the application runs, and the record of the base version is not.

        The running application is only started, and no request is made, so the mark can only come from the start
        itself, and it is announced to nobody.

        :param fx_fakes: Adapters of the test, whose database holds the book.
        :type fx_fakes: JobFakes
        :param fx_book: The stored book.
        :type fx_book: Book
        """
        uow = InMemoryUnitOfWork(fx_fakes.database)

        states = [
            (await uow.page_stages.get(PageStageKey(fx_book.page.id, stage))).state
            for stage in (Stage.PAGE_SPLIT, Stage.GEOMETRY)
        ]

        assert (states, fx_fakes.events.published) == ([StageState.FRESH, StageState.STALE], [])
