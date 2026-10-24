"""Tests for the layout of storage keys."""

from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import Rendition
from bookreviver.domain.ids import BookId, JobId, PageId, SourceId, StepId, StorageKey
from bookreviver.domain.keys import BookKeys
from bookreviver.domain.values import ProcessorRef, Renditions
from tests.helpers.builders import make_page_version, make_scan, make_source

BOOK_ID: BookId = BookId(uuid4())
KEYS: BookKeys = BookKeys()
# The areas a key of a book starts at
AREAS: frozenset[str] = frozenset({'incoming', 'sources', 'assets'})
PAGE_ID: PageId = PageId(uuid4())
STEP_ID: StepId = StepId(uuid4())
JOB_ID: JobId = JobId(uuid4())
SCAN_NUMBER: int = 12
RENDITION_ARG: str = 'rendition'


class TestRelative:
    """Tests for the keys of a book, which name every file from the directory of the book."""

    def test_no_built_key_names_the_directory_of_its_book(self) -> None:
        """Verify every area, source, scan and page key starts at an area of the book, never at ``projects/``."""
        source = make_source()
        keys = [
            KEYS.incoming_area,
            KEYS.sources_area,
            KEYS.assets_area,
            KEYS.book,
            KEYS.incoming(JOB_ID),
            KEYS.source(source.id),
            KEYS.source_scans(source.id),
            KEYS.scan_rendition(make_scan(source=source, number=0), Rendition.FULL_JPEG),
            KEYS.page(PAGE_ID),
            KEYS.version_rendition(make_page_version(page_id=PAGE_ID), Rendition.TILES),
            KEYS.page_edits(PAGE_ID, STEP_ID),
        ]
        assert [key for key in keys if key.split('/')[0] not in AREAS] == []


class TestAreas:
    """Tests for the areas the two storage ports own."""

    def test_source_files_and_derived_files_never_share_a_prefix(self) -> None:
        """Verify an upload and a source lie outside the assets area, so deleting assets never reaches them."""
        source_id = make_source().id
        expect(KEYS.incoming(JOB_ID).startswith(f'{KEYS.incoming_area}/'))
        expect(KEYS.source(source_id).startswith(f'{KEYS.sources_area}/'))
        expect(not KEYS.source(source_id).startswith(KEYS.assets_area))
        expect(not KEYS.incoming(JOB_ID).startswith(KEYS.assets_area))
        expect(KEYS.source_scans(source_id).startswith(f'{KEYS.assets_area}/'))
        expect(KEYS.book.startswith(f'{KEYS.assets_area}/'))
        assert_expectations()


class TestScanRendition:
    """Tests for BookKeys.scan_rendition()."""

    @pytest.mark.parametrize(RENDITION_ARG, list(Rendition))
    def test_key_follows_the_layout(self, rendition: Rendition) -> None:
        """Verify a scan's rendition lies under its source, its number and its renditions version.

        :param rendition: Derived file of the scan.
        :type rendition: Rendition
        """
        source = make_source()
        scan = make_scan(source=source, number=SCAN_NUMBER)
        expected = f'assets/scans/{source.id}/{SCAN_NUMBER}/v1/{rendition}'
        assert KEYS.scan_rendition(scan, rendition) == expected

    def test_key_changes_with_renditions_version(self) -> None:
        """Verify renditions cut again get new keys, so cached URLs never serve stale images."""
        scan = make_scan(source=make_source(), number=0)
        again = evolve(scan, renditions=Renditions(version=2))
        first, second = KEYS.scan_rendition(scan, Rendition.TILES), KEYS.scan_rendition(again, Rendition.TILES)
        assert (first.endswith('/0/v1/iiif'), second.endswith('/0/v2/iiif')) == (True, True)


class TestScanDirectory:
    """Tests for BookKeys.scan_directory()."""

    def test_directory_holds_every_rendition_of_the_scan(self) -> None:
        """Verify the directory of a scan's renditions is the parent of each rendition's key, so one prefix removes them."""
        scan = make_scan(source=make_source(), number=SCAN_NUMBER)
        directory = KEYS.scan_directory(scan)
        expect(directory == f'assets/scans/{scan.source_id}/{SCAN_NUMBER}/v1')
        expect(all(KEYS.scan_rendition(scan, rendition) == f'{directory}/{rendition}' for rendition in Rendition))
        assert_expectations()


class TestVersionDirectory:
    """Tests for BookKeys.version_directory()."""

    def test_directory_holds_every_rendition_of_the_version(self) -> None:
        """Verify the directory of a version is the parent of each rendition's key, so one prefix removes them."""
        version = make_page_version(page_id=PAGE_ID)
        directory = KEYS.version_directory(version)
        expect(directory == f'assets/pages/{PAGE_ID}/page-split/split.none/{version.id}')
        expect(all(KEYS.version_rendition(version, rendition) == f'{directory}/{rendition}' for rendition in Rendition))
        assert_expectations()


class TestVersionRendition:
    """Tests for BookKeys.version_rendition()."""

    def test_key_follows_the_layout(self) -> None:
        """Verify a version's rendition lies under its page, stage, processor and identifier, not under a position."""
        version = make_page_version(page_id=PAGE_ID)
        key = KEYS.version_rendition(version, Rendition.PREVIEW)
        assert key == f'assets/pages/{PAGE_ID}/page-split/split.none/{version.id}/preview.jpg'

    @pytest.mark.parametrize('processor_key', ['..', '.', 'geometry/deskew'], ids=['parent', 'self', 'separator'])
    def test_unsafe_processor_key_is_refused(self, processor_key: str) -> None:
        """Verify a processor key that would leave or split the version's directory builds no key.

        :param processor_key: Processor key that is no safe directory name.
        :type processor_key: str
        """
        version = evolve(make_page_version(page_id=PAGE_ID), processor=ProcessorRef(key=processor_key, version='1'))
        with pytest.raises(ValueError, match='segments'):
            KEYS.version_rendition(version, Rendition.FULL_PNG)


class TestPageEdits:
    """Tests for BookKeys.page_edits()."""

    def test_edits_lie_beside_the_versions_of_the_page(self) -> None:
        """Verify the edits of a page are removed with the page's directory."""
        key = KEYS.page_edits(PAGE_ID, STEP_ID)
        assert (key, key.startswith(f'{KEYS.page(PAGE_ID)}/')) == (
            f'assets/pages/{PAGE_ID}/edits/{STEP_ID}',
            True,
        )


class TestIsAsset:
    """Tests for BookKeys.is_asset()."""

    @pytest.mark.parametrize(RENDITION_ARG, list(Rendition))
    def test_built_key_is_a_derived_file(self, rendition: Rendition) -> None:
        """Verify the key of every rendition of a scan is recognised as a derived file.

        :param rendition: Derived file of the scan.
        :type rendition: Rendition
        """
        scan = make_scan(source=make_source(), number=0)
        assert BookKeys.is_asset(KEYS.scan_rendition(scan, rendition))

    @pytest.mark.parametrize(
        'key',
        [
            f'sources/{BOOK_ID}/book.pdf',
            f'projects/{BOOK_ID}/assets/book',
            'assets/../sources/book.pdf',
            'assets/./book',
            'assets//book',
            'assets',
            f'{KEYS.source(SourceId(uuid4()))}/book.pdf',
            f'{KEYS.incoming(JOB_ID)}/book.pdf',
            'notes/assets/book',
            '',
        ],
        ids=[
            'source-area',
            'with-the-book-directory',
            'climbs-out',
            'dot-segment',
            'empty-segment',
            'assets-directory-itself',
            'source-file',
            'staged-upload',
            'assets-below-another-directory',
            'empty',
        ],
    )
    def test_key_outside_the_derived_files_is_no_asset(self, key: str) -> None:
        """Verify a key that is not safely inside the derived files of a book is no asset.

        A source file and a staged upload lie inside the book but belong to the source store, so they are refused
        like a key that climbs out of it, and so is a key that still names the directory of its book.

        :param key: Key that is not a well-formed key of a derived file.
        :type key: str
        """
        assert not BookKeys.is_asset(StorageKey(key))
