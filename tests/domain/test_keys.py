"""Tests for the layout of storage keys."""

from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import Rendition
from bookreviver.domain.ids import JobId, PageId, ProjectId, SourceId, StorageKey
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import ProcessorRef, Renditions
from tests.helpers.builders import make_page_version, make_scan, make_source

PROJECT_ID: ProjectId = ProjectId(uuid4())
OTHER_PROJECT_ID: ProjectId = ProjectId(uuid4())
KEYS: ProjectKeys = ProjectKeys(PROJECT_ID)
PREFIX: StorageKey = KEYS.prefix
PAGE_ID: PageId = PageId(uuid4())
JOB_ID: JobId = JobId(uuid4())
SCAN_NUMBER: int = 12
RENDITION_ARG: str = 'rendition'


class TestPrefix:
    """Tests for ProjectKeys.prefix."""

    def test_every_built_key_lies_under_the_prefix(self) -> None:
        """Verify every area, source, scan and page key of the project starts with its prefix."""
        source = make_source(project_id=PROJECT_ID)
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
            KEYS.page_edits(PAGE_ID, 'cleanup.eraser'),
        ]
        assert [key for key in keys if not key.startswith(PREFIX)] == []

    def test_prefix_does_not_cover_a_project_whose_id_extends_it(self) -> None:
        """Verify the prefix ends with the separator, so it never matches a longer sibling name."""
        assert not StorageKey(f'{PREFIX[:-1]}x/assets').startswith(PREFIX)


class TestAreas:
    """Tests for the areas the two storage ports own."""

    def test_source_files_and_derived_files_never_share_a_prefix(self) -> None:
        """Verify an upload and a source lie outside the assets area, so deleting assets never reaches them."""
        source_id = make_source(project_id=PROJECT_ID).id
        expect(KEYS.incoming(JOB_ID).startswith(f'{KEYS.incoming_area}/'))
        expect(KEYS.source(source_id).startswith(f'{KEYS.sources_area}/'))
        expect(not KEYS.source(source_id).startswith(KEYS.assets_area))
        expect(not KEYS.incoming(JOB_ID).startswith(KEYS.assets_area))
        expect(KEYS.source_scans(source_id).startswith(f'{KEYS.assets_area}/'))
        expect(KEYS.book.startswith(f'{KEYS.assets_area}/'))
        assert_expectations()


class TestScanRendition:
    """Tests for ProjectKeys.scan_rendition()."""

    @pytest.mark.parametrize(RENDITION_ARG, list(Rendition))
    def test_key_follows_the_layout(self, rendition: Rendition) -> None:
        """Verify a scan's rendition lies under its source, its number and its renditions version.

        :param rendition: Derived file of the scan.
        :type rendition: Rendition
        """
        source = make_source(project_id=PROJECT_ID)
        scan = make_scan(source=source, number=SCAN_NUMBER)
        expected = f'{PREFIX}assets/scans/{source.id}/{SCAN_NUMBER}/v1/{rendition}'
        assert KEYS.scan_rendition(scan, rendition) == expected

    def test_key_changes_with_renditions_version(self) -> None:
        """Verify renditions cut again get new keys, so cached URLs never serve stale images."""
        scan = make_scan(source=make_source(project_id=PROJECT_ID), number=0)
        again = evolve(scan, renditions=Renditions(version=2))
        first, second = KEYS.scan_rendition(scan, Rendition.TILES), KEYS.scan_rendition(again, Rendition.TILES)
        assert (first.endswith('/0/v1/iiif'), second.endswith('/0/v2/iiif')) == (True, True)

    def test_scan_of_another_project_is_refused(self) -> None:
        """Verify a scan of another project gets no key here, so its files never land in the wrong book."""
        scan = make_scan(source=make_source(project_id=OTHER_PROJECT_ID), number=0)
        with pytest.raises(ValueError, match=str(OTHER_PROJECT_ID)):
            KEYS.scan_rendition(scan, Rendition.FULL_JPEG)


class TestScanDirectory:
    """Tests for ProjectKeys.scan_directory()."""

    def test_directory_holds_every_rendition_of_the_scan(self) -> None:
        """Verify the directory of a scan's renditions is the parent of each rendition's key, so one prefix removes them."""
        scan = make_scan(source=make_source(project_id=PROJECT_ID), number=SCAN_NUMBER)
        directory = KEYS.scan_directory(scan)
        expect(directory == f'{PREFIX}assets/scans/{scan.source_id}/{SCAN_NUMBER}/v1')
        expect(all(KEYS.scan_rendition(scan, rendition) == f'{directory}/{rendition}' for rendition in Rendition))
        assert_expectations()

    def test_directory_of_a_scan_of_another_project_is_refused(self) -> None:
        """Verify a scan of another project gets no directory here, so nothing of it is removed from this book."""
        scan = make_scan(source=make_source(project_id=OTHER_PROJECT_ID), number=0)
        with pytest.raises(ValueError, match=str(OTHER_PROJECT_ID)):
            KEYS.scan_directory(scan)


class TestVersionDirectory:
    """Tests for ProjectKeys.version_directory()."""

    def test_directory_holds_every_rendition_of_the_version(self) -> None:
        """Verify the directory of a version is the parent of each rendition's key, so one prefix removes them."""
        version = make_page_version(page_id=PAGE_ID)
        directory = KEYS.version_directory(version)
        expect(directory == f'{PREFIX}assets/pages/{PAGE_ID}/page-split/split.none/{version.id}')
        expect(all(KEYS.version_rendition(version, rendition) == f'{directory}/{rendition}' for rendition in Rendition))
        assert_expectations()


class TestVersionRendition:
    """Tests for ProjectKeys.version_rendition()."""

    def test_key_follows_the_layout(self) -> None:
        """Verify a version's rendition lies under its page, stage, processor and identifier, not under a position."""
        version = make_page_version(page_id=PAGE_ID)
        key = KEYS.version_rendition(version, Rendition.PREVIEW)
        assert key == f'{PREFIX}assets/pages/{PAGE_ID}/page-split/split.none/{version.id}/preview.jpg'

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
    """Tests for ProjectKeys.page_edits()."""

    def test_edits_lie_beside_the_versions_of_the_page(self) -> None:
        """Verify the edits of a page are removed with the page's directory."""
        key = KEYS.page_edits(PAGE_ID, 'cleanup.eraser')
        assert (key, key.startswith(f'{KEYS.page(PAGE_ID)}/')) == (
            f'{PREFIX}assets/pages/{PAGE_ID}/edits/cleanup.eraser',
            True,
        )


class TestOwning:
    """Tests for ProjectKeys.owning()."""

    @pytest.mark.parametrize(RENDITION_ARG, list(Rendition))
    def test_built_key_belongs_to_its_project(self, rendition: Rendition) -> None:
        """Verify the key of every rendition of a scan is recognised as the project's.

        :param rendition: Derived file of the scan.
        :type rendition: Rendition
        """
        scan = make_scan(source=make_source(project_id=PROJECT_ID), number=0)
        assert ProjectKeys.owning(KEYS.scan_rendition(scan, rendition)) == KEYS

    @pytest.mark.parametrize(
        'key',
        [
            f'sources/{PROJECT_ID}/book.pdf',
            'projects/not-an-id/assets/book',
            PREFIX[:-1],
            PREFIX,
            f'{PREFIX}../{OTHER_PROJECT_ID}/assets/book',
            f'{PREFIX}./assets/book',
            f'{PREFIX}/assets',
            f'{PREFIX}assets',
            f'{KEYS.source(SourceId(uuid4()))}/book.pdf',
            f'{KEYS.incoming(JOB_ID)}/book.pdf',
            f'{PREFIX}notes/assets/book',
            '',
        ],
        ids=[
            'other-root',
            'bad-id',
            'project-itself',
            'trailing-slash',
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
    def test_malformed_key_belongs_to_no_project(self, key: str) -> None:
        """Verify a key that is not safely inside a project's derived files is not attributed to any project.

        A source file and a staged upload lie inside the project but belong to the source store, so they are refused
        like a key that climbs out of it.

        :param key: Key that is not a well-formed key of a derived file.
        :type key: str
        """
        assert ProjectKeys.owning(StorageKey(key)) is None
