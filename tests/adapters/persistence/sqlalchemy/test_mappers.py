"""Tests for the SQLAlchemy mappers: every field of an entity survives a trip through the database."""

import hashlib
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.persistence.sqlalchemy.mappers import ProjectMapper
from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from bookreviver.domain.enums import (
    ColorMode,
    FileType,
    ImagePolicy,
    JobState,
    PageKind,
    SourceKind,
    TransformKind,
    VersionState,
)
from bookreviver.domain.ids import StorageKey
from bookreviver.domain.values import (
    MetadataSuggestion,
    Point,
    ProcessorRef,
    Progress,
    Quad,
    Renditions,
    ScanFacts,
    SourceFile,
    Transform,
)
from tests.helpers.builders import (
    EPOCH,
    FULL_DETAILS,
    make_job,
    make_page,
    make_page_version,
    make_project,
    make_scan,
    make_source,
    new_account_id,
)

if TYPE_CHECKING:
    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.ids import AccountId

pytestmark = pytest.mark.anyio

# The right half of a spread 2200 px wide and 1561 px high
HALF_QUAD: Quad = Quad(
    top_left=Point(x=1100.5, y=0),
    top_right=Point(x=2200, y=0),
    bottom_right=Point(x=2200, y=1561),
    bottom_left=Point(x=1100.5, y=1561),
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

    async def test_every_field_survives_the_database(self, fx_database: SqlDatabase, fx_owner_id: AccountId) -> None:
        """Verify a project with a full description, a lossless policy and a cover page reads back unchanged.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        """
        project = make_project(owner_id=fx_owner_id)
        cover = make_page(project_id=project.id)
        described = evolve(project, details=FULL_DETAILS, image_policy=ImagePolicy.LOSSLESS, cover_page_id=cover.id)
        async with fx_database.sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            await uow.projects.add(project)
            await uow.pages.add(cover)
            await uow.projects.update(described)
            await uow.commit()
        async with fx_database.sessions() as session:
            assert await SqlAlchemyUnitOfWork(session).projects.get(project.id) == described

    async def test_lists_are_stored_as_json_of_plain_values(self) -> None:
        """Verify contributors and identifiers become lists of objects holding role and scheme values, not members."""
        row = ProjectMapper().to_row(evolve(make_project(owner_id=new_account_id()), details=FULL_DETAILS))
        expect(row.contributors[:2] == [{'name': 'Я. Карскі', 'role': 'aut'}, {'name': 'И. И. Ивановъ', 'role': 'edt'}])
        expect(
            row.identifiers[:2]
            == [{'scheme': 'shelfmark', 'value': '18.123.4.56'}, {'scheme': 'isbn', 'value': '0306406152'}]
        )
        expect((row.languages, row.parallel_titles) == (['bel', 'rus'], ['Białoruskie baśnie ludowe']))
        assert_expectations()


class TestPageMapper:
    """Tests for PageMapper."""

    async def test_every_field_survives_the_database(self, fx_database: SqlDatabase, fx_owner_id: AccountId) -> None:
        """Verify an excluded right half of a spread with its label, kind and notes reads back unchanged.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        """
        project = make_project(owner_id=fx_owner_id)
        source = make_source(project_id=project.id)
        scan = make_scan(source=source, number=11)
        page = evolve(
            make_page(project_id=project.id, order_key='a0V', scan=scan),
            label='xii',
            kind=PageKind.PLATE,
            slot=2,
            included=False,
            notes='Colour chart of the scanner',
            updated_at=EPOCH + timedelta(minutes=5),
        )
        async with fx_database.sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            await uow.projects.add(project)
            await uow.sources.add(source)
            await uow.scans.add(scan)
            await uow.pages.add(page)
            await uow.commit()
        async with fx_database.sessions() as session:
            assert await SqlAlchemyUnitOfWork(session).pages.get(page.id) == page


class TestPageVersionMapper:
    """Tests for PageVersionMapper."""

    @pytest.mark.parametrize(
        'renditions', [Renditions(ready=True), Renditions(), None], ids=['ready', 'not-ready', 'no-image']
    )
    async def test_every_field_survives_the_database(
        self, fx_database: SqlDatabase, fx_owner_id: AccountId, renditions: Renditions | None
    ) -> None:
        """Verify a split version with its input, parameters, crop transform, data and renditions reads back unchanged.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        :param renditions: Renditions state of the version, None for a step without an image.
        :type renditions: Renditions | None
        """
        project = make_project(owner_id=fx_owner_id)
        page = make_page(project_id=project.id)
        base = make_page_version(page_id=page.id)
        version = evolve(
            make_page_version(page_id=page.id, minutes=1),
            processor=ProcessorRef(key='split.spread', version='1.2'),
            input_id=base.id,
            params={'spine': 'auto', 'margin_px': 12},
            transform=Transform(kind=TransformKind.CROP, quad=HALF_QUAD),
            data={'spine_x': 1100.5, 'confidence': 0.93},
            renditions=renditions,
            state=VersionState.READY,
        )
        async with fx_database.sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            await uow.projects.add(project)
            await uow.pages.add(page)
            await uow.page_versions.add_many([base, version])
            await uow.commit()
        async with fx_database.sessions() as session:
            assert await SqlAlchemyUnitOfWork(session).page_versions.get(version.id) == version

    @pytest.mark.parametrize(
        'transform',
        [
            Transform(kind=TransformKind.ROTATE, angle=-0.8),
            Transform(kind=TransformKind.PERSPECTIVE, quad=HALF_QUAD),
            Transform(kind=TransformKind.MESH, mesh_key=StorageKey('projects/x/assets/pages/y/edits/mesh')),
        ],
        ids=['rotate', 'perspective', 'mesh'],
    )
    async def test_every_transform_survives_the_database(
        self, fx_database: SqlDatabase, fx_owner_id: AccountId, transform: Transform
    ) -> None:
        """Verify each kind of transform reads back with its argument.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        :param transform: Transform of the version.
        :type transform: Transform
        """
        project = make_project(owner_id=fx_owner_id)
        page = make_page(project_id=project.id)
        version = evolve(make_page_version(page_id=page.id), transform=transform)
        async with fx_database.sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            await uow.projects.add(project)
            await uow.pages.add(page)
            await uow.page_versions.add(version)
            await uow.commit()
        async with fx_database.sessions() as session:
            assert (await SqlAlchemyUnitOfWork(session).page_versions.get(version.id)).transform == transform


class TestSourceMapper:
    """Tests for SourceMapper."""

    async def test_every_field_survives_the_database(self, fx_database: SqlDatabase, fx_owner_id: AccountId) -> None:
        """Verify an indirect DjVu source with its files, metadata, suggestion and import job reads back unchanged.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        """
        project = make_project(owner_id=fx_owner_id)
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

    async def test_every_field_survives_the_database(self, fx_database: SqlDatabase, fx_owner_id: AccountId) -> None:
        """Verify a scan with every fact, a label and ready renditions reads back equal to what was stored.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        """
        project = make_project(owner_id=fx_owner_id)
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

    async def test_every_field_survives_the_database(self, fx_database: SqlDatabase, fx_owner_id: AccountId) -> None:
        """Verify a finished job with progress, an error and its timestamps reads back equal to what was stored.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        """
        project = make_project(owner_id=fx_owner_id)
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
