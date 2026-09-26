"""Tests for the SQLAlchemy mappers: every field of an entity survives a trip through the database."""

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from attrs import evolve

from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from bookreviver.domain.enums import ColorMode, JobState, Orthography, SourceKind
from bookreviver.domain.values import BookDetails, PageAssets, PageFacts, Progress, SourceSummary
from tests.helpers.builders import EPOCH, make_job, make_page, make_project, new_account_id

if TYPE_CHECKING:
    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import Project

pytestmark = pytest.mark.anyio

FULL_DETAILS: BookDetails = BookDetails(
    title='Беларускія народныя казкі',
    authors='Я. Карскі',
    publisher='Друкарня Губернскага праўлення',
    publication_place='Вільня',
    publication_year='1905',
    edition='Выданне другое',
    series='Этнаграфічны зборнік',
    volume='II',
    language='be',
    orthography=Orthography.PRE_REFORM,
    notes='Scanned from the library copy',
)
FULL_SOURCE: SourceSummary = SourceSummary(
    kind=SourceKind.PDF,
    name='kazki.pdf',
    size_bytes=123_456_789,
    metadata={'producer': 'ABBYY', 'pages': 312, 'keywords': ['folklore', 'казкі'], 'xmp': {'rights': None}},
    imported_at=EPOCH + timedelta(hours=1),
)
FULL_FACTS: PageFacts = PageFacts(
    width_px=2480,
    height_px=3508,
    color_mode=ColorMode.BILEVEL,
    dpi_x=300.5,
    dpi_y=299.75,
    bits_per_component=1,
    image_format='jbig2',
    width_mm=210.0,
    height_mm=297.0,
    has_text_layer=True,
    source_file='page-0001.tif',
    extra={'filter': 'JBIG2Decode', 'rotation': 90},
)
FULL_PROGRESS: Progress = Progress(done=7, total=12)


class TestProjectMapper:
    """Tests for ProjectMapper."""

    async def test_every_field_survives_the_database(self, fx_database: SqlDatabase) -> None:
        """Verify a project with a full description and a source reads back equal to what was stored."""
        project = evolve(make_project(owner_id=new_account_id()), details=FULL_DETAILS, source=FULL_SOURCE)
        async with fx_database.sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            await uow.projects.add(project)
            await uow.commit()
        async with fx_database.sessions() as session:
            assert await SqlAlchemyUnitOfWork(session).projects.get(project.id) == project

    async def test_update_can_remove_the_source(self, fx_database: SqlDatabase) -> None:
        """Verify updating a project to have no source stores no source, rather than keeping the old one."""
        project: Project = evolve(make_project(owner_id=new_account_id()), source=FULL_SOURCE)
        async with fx_database.sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            await uow.projects.add(project)
            await uow.projects.update(evolve(project, source=None))
            await uow.commit()
        async with fx_database.sessions() as session:
            assert (await SqlAlchemyUnitOfWork(session).projects.get(project.id)).source is None


class TestPageMapper:
    """Tests for PageMapper."""

    async def test_every_field_survives_the_database(self, fx_database: SqlDatabase) -> None:
        """Verify a page with every fact, extra metadata and ready assets reads back equal to what was stored."""
        project = make_project(owner_id=new_account_id())
        page = evolve(
            make_page(project_id=project.id, index=4), facts=FULL_FACTS, assets=PageAssets(ready=True, version=3)
        )
        async with fx_database.sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            await uow.projects.add(project)
            await uow.pages.replace_for_project(project.id, [page])
            await uow.commit()
        async with fx_database.sessions() as session:
            assert await SqlAlchemyUnitOfWork(session).pages.get(project.id, page.index) == page


class TestJobMapper:
    """Tests for JobMapper."""

    async def test_every_field_survives_the_database(self, fx_database: SqlDatabase) -> None:
        """Verify a finished job with progress, an error and its timestamps reads back equal to what was stored."""
        project = make_project(owner_id=new_account_id())
        job = evolve(
            make_job(project_id=project.id, state=JobState.FAILED),
            progress=FULL_PROGRESS,
            error='Page 8 could not be rasterised',
            started_at=EPOCH + timedelta(minutes=1),
            finished_at=EPOCH + timedelta(minutes=5),
        )
        async with fx_database.sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            await uow.projects.add(project)
            await uow.jobs.add(job)
            await uow.commit()
        async with fx_database.sessions() as session:
            assert await SqlAlchemyUnitOfWork(session).jobs.get(job.id) == job
