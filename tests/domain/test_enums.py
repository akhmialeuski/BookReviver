"""Tests for the domain enums."""

import pytest

from bookreviver.domain.enums import FileType, JobState, SourceKind, Stage, UploadProblem
from bookreviver.domain.errors import UploadRejectedError

# Written out rather than taken from the enum, so the test pins the value stored and sent over the API
PAGE_SPLIT_VALUE: str = 'page-split'
EXPECTED_ARG: str = 'expected'
NAMES_ARG: str = 'names'
PDF_NAME: str = 'book.pdf'
DJVU_NAME: str = 'book.djvu'
TIFF_NAME: str = 'page.tif'
COVER_NAME: str = 'cover.jpg'


class TestLabeledStrEnum:
    """Tests for LabeledStrEnum members."""

    def test_member_is_its_value_and_carries_label(self) -> None:
        """Verify a member compares equal to its value, parses from it and keeps its label."""
        assert (Stage.PAGE_SPLIT == PAGE_SPLIT_VALUE, Stage(PAGE_SPLIT_VALUE), Stage.PAGE_SPLIT.label) == (
            True,
            Stage.PAGE_SPLIT,
            'Page split',
        )


class TestStage:
    """Tests for the order of the Stage members."""

    def test_page_order_follows_page_split(self) -> None:
        """Verify the page order stage runs right after the split and before every stage that works on pages."""
        stages = list(Stage)
        assert stages[stages.index(Stage.PAGE_SPLIT) + 1] is Stage.PAGE_ORDER


class TestFileType:
    """Tests for FileType.from_name()."""

    @pytest.mark.parametrize(
        ('name', EXPECTED_ARG),
        [
            ('book.PDF', FileType.PDF),
            ('scan_001.tif', FileType.TIFF),
            ('scan.TIFF', FileType.TIFF),
            ('page.jpeg', FileType.JPEG),
            ('page.png', FileType.PNG),
            ('page.jp2', FileType.JPEG_2000),
            ('page.J2K', FileType.JPEG_2000),
            (DJVU_NAME, FileType.DJVU),
            ('book.djv', FileType.DJVU),
            ('notes.txt', None),
            ('no-suffix', None),
        ],
    )
    def test_detects_type_by_suffix(self, name: str, expected: FileType | None) -> None:
        """Verify the suffix decides the type, case-insensitively, and unknown suffixes give None.

        :param name: File name to classify.
        :type name: str
        :param expected: Type the name must give, or None for an unaccepted suffix.
        :type expected: FileType | None
        """
        assert FileType.from_name(name) == expected

    @pytest.mark.parametrize(
        ('file_type', 'kind'),
        [
            (FileType.PDF, SourceKind.PDF),
            (FileType.DJVU, SourceKind.DJVU),
            (FileType.TIFF, SourceKind.IMAGES),
            (FileType.JPEG, SourceKind.IMAGES),
            (FileType.JPEG_2000, SourceKind.IMAGES),
            (FileType.PNG, SourceKind.IMAGES),
        ],
    )
    def test_source_kind(self, file_type: FileType, kind: SourceKind) -> None:
        """Verify a PDF and a DjVu file make a source of their own kind, and every image type makes an image source.

        :param file_type: Accepted file type.
        :type file_type: FileType
        :param kind: Source kind the file type must make.
        :type kind: SourceKind
        """
        assert file_type.source_kind == kind


class TestSourceKindOfFiles:
    """Tests for SourceKind.of_files()."""

    @pytest.mark.parametrize(
        (NAMES_ARG, EXPECTED_ARG),
        [
            ((PDF_NAME,), SourceKind.PDF),
            # A book split into parts, one PDF per part
            (('part1.pdf', 'part2.PDF'), SourceKind.PDF),
            # A bundled DjVu document holds the whole book in one file
            (('book.DJVU',), SourceKind.DJVU),
            # An indirect DjVu document is an index file and one file per page
            (('index.djvu', 'p0001.djvu', 'p0002.djv'), SourceKind.DJVU),
            ((TIFF_NAME,), SourceKind.IMAGES),
            # A directory of scans mixing every accepted image type is one image set
            (('001.tif', '002.jpg', '003.png', '004.jp2'), SourceKind.IMAGES),
        ],
        ids=['one-pdf', 'pdf-parts', 'bundled-djvu', 'indirect-djvu', 'one-image', 'mixed-images'],
    )
    def test_detects_kind_of_upload(self, names: tuple[str, ...], expected: SourceKind) -> None:
        """Verify one or several PDF files, DjVu files, and any set of page images each make a source of their kind.

        :param names: File names of the upload.
        :type names: tuple[str, ...]
        :param expected: Kind of source the upload must make.
        :type expected: SourceKind
        """
        assert SourceKind.of_files(names) == expected

    @pytest.mark.parametrize(
        (NAMES_ARG, 'problem'),
        [
            ((), UploadProblem.NO_FILES),
            ((TIFF_NAME, 'Thumbs.db'), UploadProblem.UNSUPPORTED_TYPE),
            ((PDF_NAME, COVER_NAME), UploadProblem.MIXED_TYPES),
            ((DJVU_NAME, COVER_NAME), UploadProblem.MIXED_TYPES),
            ((PDF_NAME, DJVU_NAME), UploadProblem.MIXED_TYPES),
        ],
        ids=['empty', 'unsupported', 'pdf-with-image', 'djvu-with-image', 'pdf-with-djvu'],
    )
    def test_rejects_upload_breaking_a_rule(self, names: tuple[str, ...], problem: UploadProblem) -> None:
        """Reject an empty upload, an unaccepted type, and files of different kinds.

        :param names: File names of the upload.
        :type names: tuple[str, ...]
        :param problem: The upload rule the names break.
        :type problem: UploadProblem
        """
        with pytest.raises(UploadRejectedError) as error:
            SourceKind.of_files(names)
        assert error.value.problem is problem


class TestJobState:
    """Tests for JobState.is_final and JobState.active()."""

    def test_only_finished_states_are_final(self) -> None:
        """Verify exactly the succeeded, failed and cancelled states are final."""
        assert {state for state in JobState if state.is_final} == {
            JobState.SUCCEEDED,
            JobState.FAILED,
            JobState.CANCELLED,
        }

    def test_active_states_are_queued_and_running(self) -> None:
        """Verify the active states are exactly the queued and running ones, the complement of the final states."""
        assert JobState.active() == {JobState.QUEUED, JobState.RUNNING}
