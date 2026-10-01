"""Tests for the value objects of the domain that have no test module of their own."""

from typing import NamedTuple

import pytest

from bookreviver.domain.enums import UploadProblem
from bookreviver.domain.errors import UploadRejectedError
from bookreviver.domain.values import UploadPath


class ParsedPathCase(NamedTuple):
    """A path a client sent, and what the domain makes of it.

    :ivar raw: Path as sent.
    :ivar path: The checked path as a string.
    :ivar name: The file name, the last segment.
    :ivar folders: The folders above the file, outermost first.
    """

    raw: str
    path: str
    name: str
    folders: tuple[str, ...]


class RefusedPathCase(NamedTuple):
    """A path a client sent that is refused, and why.

    :ivar raw: Path as sent.
    :ivar problem: The rule the path breaks.
    """

    raw: str
    problem: UploadProblem


class TestUploadPath:
    """Tests for UploadPath.parse() and the members of the path it returns."""

    @pytest.mark.parametrize(
        'case',
        [
            ParsedPathCase(raw='001.tif', path='001.tif', name='001.tif', folders=()),
            ParsedPathCase(raw='vol1/001.tif', path='vol1/001.tif', name='001.tif', folders=('vol1',)),
            ParsedPathCase(
                raw='book\\vol1\\001.tif', path='book/vol1/001.tif', name='001.tif', folders=('book', 'book/vol1')
            ),
            ParsedPathCase(
                raw='Том первый/001.tif', path='Том первый/001.tif', name='001.tif', folders=('Том первый',)
            ),
            ParsedPathCase(raw='vol.1/.hidden', path='vol.1/.hidden', name='.hidden', folders=('vol.1',)),
            ParsedPathCase(raw='ab:c.tif', path='ab:c.tif', name='ab:c.tif', folders=()),
        ],
        ids=['bare-name', 'folder', 'backslashes', 'non-latin-folder', 'dots-inside-names', 'colon-after-two-letters'],
    )
    def test_keeps_a_relative_path_with_slashes(self, case: ParsedPathCase) -> None:
        """Verify a path inside the chosen directory is kept as it is, with ``/`` between its segments.

        :param case: A path as sent, and what it must become.
        :type case: ParsedPathCase
        """
        path = UploadPath.parse(case.raw)

        assert (str(path), path.name, path.folders) == (case.path, case.name, case.folders)

    @pytest.mark.parametrize(
        'case',
        [
            RefusedPathCase(raw='', problem=UploadProblem.EMPTY_NAME),
            RefusedPathCase(raw='.', problem=UploadProblem.EMPTY_NAME),
            RefusedPathCase(raw='..', problem=UploadProblem.EMPTY_NAME),
            RefusedPathCase(raw='vol1/', problem=UploadProblem.EMPTY_NAME),
            RefusedPathCase(raw='vol1/..', problem=UploadProblem.EMPTY_NAME),
            RefusedPathCase(raw='../001.tif', problem=UploadProblem.UNSAFE_PATH),
            RefusedPathCase(raw='vol1/../../001.tif', problem=UploadProblem.UNSAFE_PATH),
            RefusedPathCase(raw='vol1\\..\\001.tif', problem=UploadProblem.UNSAFE_PATH),
            RefusedPathCase(raw='./001.tif', problem=UploadProblem.UNSAFE_PATH),
            RefusedPathCase(raw='/etc/001.tif', problem=UploadProblem.UNSAFE_PATH),
            RefusedPathCase(raw='\\\\server\\share\\001.tif', problem=UploadProblem.UNSAFE_PATH),
            RefusedPathCase(raw='C:\\scans\\001.tif', problem=UploadProblem.UNSAFE_PATH),
            RefusedPathCase(raw='c:/001.tif', problem=UploadProblem.UNSAFE_PATH),
            RefusedPathCase(raw='C:001.tif', problem=UploadProblem.UNSAFE_PATH),
            RefusedPathCase(raw='vol1//001.tif', problem=UploadProblem.UNSAFE_PATH),
            RefusedPathCase(raw='vol1/00\x001.tif', problem=UploadProblem.UNSAFE_PATH),
        ],
        ids=[
            'empty',
            'dot',
            'dot-dot',
            'trailing-slash',
            'folder-dot-dot',
            'parent-first',
            'parent-twice',
            'parent-with-backslashes',
            'current-folder',
            'absolute',
            'unc',
            'drive-letter',
            'drive-letter-lower-case',
            'drive-relative',
            'empty-segment',
            'control-character',
        ],
    )
    def test_refuses_a_path_that_names_no_file_or_leaves_its_folder(self, case: RefusedPathCase) -> None:
        """Verify an empty name is refused as such, and every path that could leave the folder as unsafe.

        :param case: A path as sent, and the rule it breaks.
        :type case: RefusedPathCase
        """
        with pytest.raises(UploadRejectedError) as error:
            UploadPath.parse(case.raw)

        assert error.value.problem is case.problem
