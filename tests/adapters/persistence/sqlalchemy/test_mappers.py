"""Tests for the SQLAlchemy mappers: every field of an entity survives a trip through the database."""

import hashlib
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from attrs import evolve

from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from bookreviver.domain.enums import ColorMode, FileType, JobState, Orthography, SourceKind
from bookreviver.domain.values import (
    BookDetails,
    MetadataSuggestion,
    PageAssets,
    PageFacts,
    Progress,
    Renditions,
    ScanFacts,
    SourceFile,
    SourceSummary,
)
from tests.helpers.builders import (
    EPOCH,
    make_job,
    make_page,
    make_project,
    make_scan,
    make_source,
    new_account_id,
)

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
FULL_SCAN_FACTS: ScanFacts = ScanFacts(
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
    extra={'filter': 'JBIG2Decode', 'rotation': 90},
)
FULL_FILES: list[SourceFile] = [
    SourceFile(name='index.djvu', size_bytes=2048, sha256=hashlib.sha256(b'index').hexdigest()),
    SourceFile(name='p0001.djvu', size_bytes=90_112, sha256=hashlib.sha256(b'p0001').hexdigest()),
    SourceFile(name='p0002.djvu', size_bytes=88_064, sha256=hashlib.sha256(b'p0002').hexdigest()),
]
FULL_METADATA: dict[str, Any] = {'document': 'indirect', 'pages': 2, 'meta': {'year': '1905'}, 'text_layer': False}
FULL_SUGGESTION: MetadataSuggestion = MetadataSuggestion(
    title='Беларускія народныя казкі', authors='Я. Карскі', publisher='', publication_year='1905', language='bel'
)
FULL_PROGRESS: Progress = Progress(done=7, total=12)


class TestProjectMapper:
    """Tests for ProjectMapper."""

    async def test_every_field_survives_the_database(self, fx_database: SqlDatabase) -> None:
        """Verify a project with a full description and a source reads back equal to what was stored.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        """
        project = evolve(make_project(owner_id=new_account_id()), details=FULL_DETAILS, source=FULL_SOURCE)
        async with fx_database.sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            await uow.projects.add(project)
            await uow.commit()
        async with fx_database.sessions() as session:
            assert await SqlAlchemyUnitOfWork(session).projects.get(project.id) == project

    async def test_update_can_remove_the_source(self, fx_database: SqlDatabase) -> None:
        """Verify updating a project to have no source stores no source, rather than keeping the old one.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        """
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
        """Verify a page with every fact, extra metadata and ready assets reads back equal to what was stored.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        """
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


class TestSourceMapper:
    """Tests for SourceMapper."""

    async def test_every_field_survives_the_database(self, fx_database: SqlDatabase) -> None:
        """Verify an indirect DjVu source with its files, metadata, suggestion and import job reads back unchanged.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        """
        project = make_project(owner_id=new_account_id())
        job = make_job(project_id=project.id)
        source = evolve(
            make_source(project_id=project.id),
            kind=SourceKind.DJVU,
            file_type=FileType.DJVU,
            file_name='kazki/index.djvu',
            files=FULL_FILES,
            size_bytes=sum(stored.size_bytes for stored in FULL_FILES),
            sha256=FULL_FILES[0].sha256,
            scan_count=2,
            metadata=FULL_METADATA,
            suggestion=FULL_SUGGESTION,
            import_job_id=job.id,
        )
        async with fx_database.sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            await uow.projects.add(project)
            await uow.jobs.add(job)
            await uow.sources.add(source)
            await uow.commit()
        async with fx_database.sessions() as session:
            assert await SqlAlchemyUnitOfWork(session).sources.get(source.id) == source


class TestScanMapper:
    """Tests for ScanMapper."""

    async def test_every_field_survives_the_database(self, fx_database: SqlDatabase) -> None:
        """Verify a scan with every fact, a label and ready renditions reads back equal to what was stored.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        """
        project = make_project(owner_id=new_account_id())
        source = make_source(project_id=project.id)
        scan = evolve(
            make_scan(source=source, number=11),
            source_label='xii',
            facts=FULL_SCAN_FACTS,
            renditions=Renditions(ready=True, version=3),
        )
        async with fx_database.sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            await uow.projects.add(project)
            await uow.sources.add(source)
            await uow.scans.add(scan)
            await uow.commit()
        async with fx_database.sessions() as session:
            assert await SqlAlchemyUnitOfWork(session).scans.get(scan.id) == scan


class TestJobMapper:
    """Tests for JobMapper."""

    async def test_every_field_survives_the_database(self, fx_database: SqlDatabase) -> None:
        """Verify a finished job with progress, an error and its timestamps reads back equal to what was stored.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        """
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
