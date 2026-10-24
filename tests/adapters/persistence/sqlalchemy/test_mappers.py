"""Tests for the SQLAlchemy mappers: every field of an entity survives a trip through the database."""

import hashlib
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.persistence.sqlalchemy.mappers import BookRecordMapper
from bookreviver.domain.enums import (
    ColorMode,
    ContributorRole,
    FileType,
    IdentifierScheme,
    ImagePolicy,
    PageKind,
    RejectionReason,
    Rendition,
    ReviewReason,
    SourceKind,
    SourceStatus,
    TransformKind,
    VersionState,
)
from bookreviver.domain.geometry import Point, Quad, Transform
from bookreviver.domain.ids import StorageKey
from bookreviver.domain.values import (
    BookIdentifier,
    Contributor,
    MetadataSuggestion,
    ProcessorRef,
    Renditions,
    ScanFacts,
    SourceFile,
)
from tests.helpers.builders import (
    EPOCH,
    FULL_DETAILS,
    make_page,
    make_page_version,
    make_record,
    make_scan,
    make_source,
)
from tests.helpers.seeding import store_book

if TYPE_CHECKING:
    from bookreviver.domain.ids import AccountId
    from tests.helpers.database import BookDatabasesFactory, UnitOfWorkFactory

pytestmark = pytest.mark.anyio

# The keys of the JSON objects that hold contributors and identifiers
NAME_KEY: str = 'name'
ROLE_KEY: str = 'role'
SCHEME_KEY: str = 'scheme'
VALUE_KEY: str = 'value'
# The printed number of a page, which is the same on a page and on the scan it shows
ROMAN_LABEL: str = 'xii'

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
    title='Беларускія народныя казкі',
    contributors=(
        Contributor(name='Я. Карскі', role=ContributorRole.AUTHOR),
        Contributor(name='П. П. Петровъ', role=ContributorRole.CONTRIBUTOR),
    ),
    publisher='',
    publication_year='1905',
    languages=('bel', 'rus'),
    identifiers=(
        BookIdentifier.parse(IdentifierScheme.ISBN, '978-0-306-40615-7'),
        BookIdentifier.parse(IdentifierScheme.URL, 'https://example.org/books/karski'),
    ),
    subjects=('Фольклор', 'Казкі'),
)
# The text for the reader that says more than the reason of a file that was not imported
SOURCE_DETAIL: str = 'The file type is not accepted as a source.'


class TestBookRecordMapper:
    """Tests for BookRecordMapper."""

    async def test_every_field_survives_the_database(
        self, fx_uow: UnitOfWorkFactory, fx_owner_id: AccountId, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a book with a full description, and a lossless policy reads back unchanged.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_owner_id: Committed account owning the book.
        :type fx_owner_id: AccountId
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_record = make_record()
        described = evolve(book_record, details=FULL_DETAILS, image_policy=ImagePolicy.LOSSLESS)
        uow = fx_uow()
        await store_book(uow, book_record, owner=fx_owner_id, databases=fx_databases())
        await (
            fx_databases().open(book_record.id).change(lambda repos, _book_record: repos.book_record.update(described))
        )
        assert (
            await fx_databases().open(book_record.id).read(lambda repos: repos.book_record.get(book_record.id))
            == described
        )

    async def test_lists_are_stored_as_json_of_plain_values(self) -> None:
        """Verify contributors and identifiers become lists of objects holding role and scheme values, not members."""
        row = BookRecordMapper().to_row(evolve(make_record(), details=FULL_DETAILS))
        expect(
            row.contributors[:2]
            == [{NAME_KEY: 'Я. Карскі', ROLE_KEY: 'aut'}, {NAME_KEY: 'И. И. Ивановъ', ROLE_KEY: 'edt'}]
        )
        expect(
            row.identifiers[:2]
            == [{SCHEME_KEY: 'shelfmark', VALUE_KEY: '18.123.4.56'}, {SCHEME_KEY: 'isbn', VALUE_KEY: '0306406152'}]
        )
        expect((row.languages, row.parallel_titles) == (['bel', 'rus'], ['Białoruskie baśnie ludowe']))
        assert_expectations()


class TestPageMapper:
    """Tests for PageMapper."""

    async def test_every_field_survives_the_database(
        self, fx_uow: UnitOfWorkFactory, fx_owner_id: AccountId, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify an excluded right half of a spread with its label, kind and notes reads back unchanged.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_owner_id: Committed account owning the book.
        :type fx_owner_id: AccountId
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_record = make_record()
        source = make_source()
        scan = make_scan(source=source, number=11)
        page = evolve(
            make_page(order_key='a0V', scan=scan),
            manual_label=ROMAN_LABEL,
            kind=PageKind.PLATE,
            slot=2,
            included=False,
            notes='Colour chart of the scanner',
            updated_at=EPOCH + timedelta(minutes=5),
        )
        uow = fx_uow()
        await store_book(
            uow, book_record, page, sources=[source], scans=[scan], owner=fx_owner_id, databases=fx_databases()
        )
        assert await fx_databases().open(book_record.id).read(lambda repos: repos.pages.get(page.id)) == page


class TestPageVersionMapper:
    """Tests for PageVersionMapper."""

    @pytest.mark.parametrize(
        'renditions',
        [Renditions(ready=True), Renditions(ready=True, full=Rendition.FULL_PNG), Renditions(), None],
        ids=['ready', 'ready-png', 'not-ready', 'no-image'],
    )
    async def test_every_field_survives_the_database(
        self,
        fx_uow: UnitOfWorkFactory,
        fx_owner_id: AccountId,
        renditions: Renditions | None,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify a split version with its input, parameters, crop transform, data and renditions reads back unchanged.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_owner_id: Committed account owning the book.
        :type fx_owner_id: AccountId
        :param renditions: Renditions state of the version, None for a step without an image.
        :type renditions: Renditions | None
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_record = make_record()
        page = make_page()
        base = make_page_version(page_id=page.id)
        version = evolve(
            make_page_version(page_id=page.id, minutes=1),
            processor=ProcessorRef(key='split.spread', version='1.2'),
            input_id=base.id,
            params={'spine': 'auto', 'margin_px': 12},
            transform=Transform(kind=TransformKind.CROP, quad=HALF_QUAD, matrix=(1, 0, -1100.5, 0, 1, 0, 0, 0, 1)),
            data={'spine_x': 1100.5, 'confidence': 0.93},
            review=ReviewReason.LOW_CONFIDENCE,
            renditions=renditions,
            state=VersionState.READY,
        )
        uow = fx_uow()
        await store_book(uow, book_record, page, versions=[base, version], owner=fx_owner_id, databases=fx_databases())
        assert (
            await fx_databases().open(book_record.id).read(lambda repos: repos.page_versions.get(version.id)) == version
        )

    @pytest.mark.parametrize(
        'transform',
        [
            Transform(kind=TransformKind.ROTATE, angle=-0.8, matrix=(0.99, -0.01, 3.5, 0.01, 0.99, -1.25, 0, 0, 1)),
            Transform(kind=TransformKind.CROP, quad=HALF_QUAD, matrix=(1, 0, -1100.5, 0, 1, 0, 0, 0, 1)),
            Transform(kind=TransformKind.PERSPECTIVE, quad=HALF_QUAD, matrix=(1, 0, 0, 0, 1, 0, 0.001, 0, 1)),
            Transform(kind=TransformKind.MESH, mesh_key=StorageKey('assets/pages/y/edits/mesh')),
        ],
        ids=['rotate', 'crop', 'perspective', 'mesh'],
    )
    async def test_every_transform_survives_the_database(
        self,
        fx_uow: UnitOfWorkFactory,
        fx_owner_id: AccountId,
        transform: Transform,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify each kind of transform reads back with its argument.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_owner_id: Committed account owning the book.
        :type fx_owner_id: AccountId
        :param transform: Transform of the version.
        :type transform: Transform
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_record = make_record()
        page = make_page()
        version = evolve(make_page_version(page_id=page.id), transform=transform)
        uow = fx_uow()
        await store_book(uow, book_record, page, versions=[version], owner=fx_owner_id, databases=fx_databases())
        assert (
            await fx_databases().open(book_record.id).read(lambda repos: repos.page_versions.get(version.id))
        ).transform == transform


class TestSourceMapper:
    """Tests for SourceMapper."""

    async def test_every_field_survives_the_database(
        self, fx_uow: UnitOfWorkFactory, fx_owner_id: AccountId, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify an indirect DjVu source with its files, metadata and suggestion reads back unchanged.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_owner_id: Committed account owning the book.
        :type fx_owner_id: AccountId
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_record = make_record()
        source = evolve(
            make_source(),
            kind=SourceKind.DJVU,
            file_type=FileType.DJVU,
            file_name='kazki/index.djvu',
            files=FULL_FILES,
            size_bytes=sum(stored.size_bytes for stored in FULL_FILES),
            sha256=FULL_FILES[0].sha256,
            scan_count=2,
            metadata=FULL_METADATA,
            suggestion=FULL_SUGGESTION,
        )
        # The count is read from the scans of the source, so the source is stored with as many
        scans = [make_scan(source=source, number=number) for number in range(source.scan_count)]
        uow = fx_uow()
        await store_book(uow, book_record, sources=[source], scans=scans, owner=fx_owner_id, databases=fx_databases())
        assert await fx_databases().open(book_record.id).read(lambda repos: repos.sources.get(source.id)) == source

    @pytest.mark.parametrize(
        ('status', 'rejection'),
        [(SourceStatus.REJECTED, RejectionReason.UNSUPPORTED_TYPE), (SourceStatus.SKIPPED, None)],
    )
    async def test_a_file_of_an_unknown_type_that_was_not_imported_survives_the_database(
        self,
        fx_uow: UnitOfWorkFactory,
        fx_owner_id: AccountId,
        status: SourceStatus,
        rejection: RejectionReason | None,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify a rejected or skipped file with no kind and no type, a reason and a detail reads back unchanged.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_owner_id: Committed account owning the book.
        :type fx_owner_id: AccountId
        :param status: Whether the file was rejected or skipped.
        :type status: SourceStatus
        :param rejection: The reason of a rejected file, and None for a skipped one.
        :type rejection: RejectionReason | None
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_record = make_record()
        source = evolve(
            make_source(),
            kind=None,
            file_type=None,
            scan_count=0,
            status=status,
            rejection=rejection,
            detail=SOURCE_DETAIL,
        )
        uow = fx_uow()
        await store_book(uow, book_record, sources=[source], owner=fx_owner_id, databases=fx_databases())
        assert await fx_databases().open(book_record.id).read(lambda repos: repos.sources.get(source.id)) == source


class TestScanMapper:
    """Tests for ScanMapper."""

    async def test_every_field_survives_the_database(
        self, fx_uow: UnitOfWorkFactory, fx_owner_id: AccountId, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a scan with every fact, a label and ready renditions reads back equal to what was stored.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_owner_id: Committed account owning the book.
        :type fx_owner_id: AccountId
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_record = make_record()
        source = make_source()
        scan = evolve(
            make_scan(source=source, number=11),
            source_label=ROMAN_LABEL,
            facts=FULL_SCAN_FACTS,
            renditions=Renditions(ready=True, version=3, full=Rendition.FULL_PNG),
        )
        uow = fx_uow()
        await store_book(uow, book_record, sources=[source], scans=[scan], owner=fx_owner_id, databases=fx_databases())
        assert await fx_databases().open(book_record.id).read(lambda repos: repos.scans.get(scan.id)) == scan
