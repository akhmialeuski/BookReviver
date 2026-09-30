"""Tests for the domain enums."""

import pytest

from bookreviver.domain.enums import FileType, JobState, SourceKind, Stage

# Written out rather than taken from the enum, so the test pins the value stored and sent over the API
PAGE_SPLIT_VALUE: str = 'page-split'
EXPECTED_ARG: str = 'expected'
DJVU_NAME: str = 'book.djvu'


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
            (FileType.TIFF, SourceKind.IMAGE),
            (FileType.JPEG, SourceKind.IMAGE),
            (FileType.JPEG_2000, SourceKind.IMAGE),
            (FileType.PNG, SourceKind.IMAGE),
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
