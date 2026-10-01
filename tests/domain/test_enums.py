"""Tests for the domain enums."""

import pytest

from bookreviver.domain.enums import (
    ColorMode,
    ContributorRole,
    FileType,
    ImagePolicy,
    JobState,
    Rendition,
    RightsStatus,
    Script,
    SourceKind,
    Stage,
    SystemFile,
)

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


class TestImagePolicy:
    """Tests for ImagePolicy.full_format(), the rule of decision 22 of the book model."""

    @pytest.mark.parametrize(
        ('policy', 'color_mode', EXPECTED_ARG),
        [
            (ImagePolicy.COMPACT, ColorMode.BILEVEL, Rendition.FULL_PNG),
            (ImagePolicy.LOSSLESS, ColorMode.BILEVEL, Rendition.FULL_PNG),
            (ImagePolicy.COMPACT, ColorMode.GRAY, Rendition.FULL_JPEG),
            (ImagePolicy.LOSSLESS, ColorMode.GRAY, Rendition.FULL_PNG),
            (ImagePolicy.COMPACT, ColorMode.COLOR, Rendition.FULL_JPEG),
            (ImagePolicy.LOSSLESS, ColorMode.COLOR, Rendition.FULL_PNG),
            (ImagePolicy.COMPACT, ColorMode.UNKNOWN, Rendition.FULL_JPEG),
            (ImagePolicy.LOSSLESS, ColorMode.UNKNOWN, Rendition.FULL_PNG),
        ],
    )
    def test_full_format(self, policy: ImagePolicy, color_mode: ColorMode, expected: Rendition) -> None:
        """Verify a bilevel page is a PNG under both policies and any other page follows the policy.

        :param policy: Image policy of the project.
        :type policy: ImagePolicy
        :param color_mode: Colour mode of the page.
        :type color_mode: ColorMode
        :param expected: Format of the ``full`` image the rule must give.
        :type expected: Rendition
        """
        assert policy.full_format(color_mode) == expected


class TestSystemFile:
    """Tests for SystemFile.matches()."""

    @pytest.mark.parametrize(
        ('name', EXPECTED_ARG),
        [
            ('Thumbs.db', True),
            ('THUMBS.DB', True),
            ('desktop.ini', True),
            ('.DS_Store', True),
            ('._001.tif', True),
            ('vol1/Thumbs.db', True),
            ('vol1\\scans\\.DS_Store', True),
            ('vol1/._001.tif', True),
            ('001.tif', False),
            ('thumbs.db.pdf', False),
            ('my.DS_Store.png', False),
            ('vol.1/001.tif', False),
            ('Thumbs.db/001.tif', False),
            ('a._b.tif', False),
        ],
        ids=[
            'thumbs',
            'thumbs-upper-case',
            'desktop-ini',
            'ds-store',
            'apple-double',
            'thumbs-in-folder',
            'ds-store-in-backslash-folder',
            'apple-double-in-folder',
            'scan',
            'longer-name',
            'name-inside-a-longer-one',
            'dot-in-folder',
            'system-name-as-folder',
            'prefix-not-at-start',
        ],
    )
    def test_matches_the_name_of_the_file_and_not_its_folders(self, *, name: str, expected: bool) -> None:
        """Verify a system file is found by its last path segment in any letter case, and a folder name never matches.

        :param name: File name or relative path to classify.
        :type name: str
        :param expected: Whether the name is a system file.
        :type expected: bool
        """
        assert SystemFile.matches(name) is expected

    def test_every_member_has_a_label(self) -> None:
        """Verify every known system file says what it is, as the interface shows it to the user."""
        assert all(member.label for member in SystemFile)


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


class TestContributorRole:
    """Tests for the members of ContributorRole."""

    def test_codes_and_terms_are_those_of_the_marc_list_of_relators(self) -> None:
        """Verify each member has the code and the term the MARC Code List for Relators gives, written out here."""
        assert {role.value: role.label for role in ContributorRole} == {
            'aut': 'author',
            'edt': 'editor',
            'com': 'compiler',
            'trl': 'translator',
            'ill': 'illustrator',
            'egr': 'engraver',
            'ltg': 'lithographer',
            'pht': 'photographer',
            'wpr': 'writer of preface',
            'win': 'writer of introduction',
            'ann': 'annotator',
            'cmm': 'commentator',
            'dte': 'dedicatee',
            'ctb': 'contributor',
            'oth': 'other',
        }


class TestScriptAndRightsStatus:
    """Tests for the values and labels of Script and RightsStatus."""

    def test_values_and_labels(self) -> None:
        """Verify the stored values and the labels of the writing systems and the rights statuses."""
        assert ({s.value: s.label for s in Script}, {r.value: r.label for r in RightsStatus}) == (
            {'unknown': 'Unknown', 'cyrillic': 'Cyrillic', 'latin': 'Latin', 'mixed': 'Mixed'},
            {'unknown': 'Unknown', 'public-domain': 'Public domain', 'in-copyright': 'In copyright'},
        )
