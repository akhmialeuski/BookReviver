"""Tests for the split.none processor."""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import ColorMode, TransformKind
from bookreviver.domain.errors import ConflictError, InvalidParametersError
from bookreviver.plugins.split_none import SplitNone
from bookreviver.ports.processing import StepInput

if TYPE_CHECKING:
    from pathlib import Path

SCAN_FACTS: dict[str, object] = {'width_px': 2200, 'height_px': 1561, 'dpi': 300.0, 'color_mode': 'gray'}


class TestSplitNone:
    """Tests for SplitNone."""

    def test_returns_the_scan_unchanged_with_its_size(self, tmp_path: Path) -> None:
        """Verify the whole scan is the page: the same file, the identity transform and the size in the data.

        :param tmp_path: Temporary directory of the test, holding the work directory.
        :type tmp_path: Path
        """
        scan = tmp_path / 'full.jpg'
        scan.write_bytes(b'scan')
        result = SplitNone().run(StepInput(image=scan, input_data=SCAN_FACTS, workdir=tmp_path))
        [output] = result.outputs
        expect(output.image == scan)
        expect(output.color_mode is ColorMode.GRAY)
        expect(output.transform.kind is TransformKind.IDENTITY)
        expect(output.data == {'width_px': 2200, 'height_px': 1561, 'dpi': 300.0})
        assert_expectations()

    def test_step_without_an_image_is_rejected(self, tmp_path: Path) -> None:
        """Reject a run that has no scan image, since there is nothing to copy.

        :param tmp_path: Temporary directory of the test, holding the work directory.
        :type tmp_path: Path
        """
        with pytest.raises(ConflictError, match='image of a scan'):
            SplitNone().run(StepInput(image=None, input_data=SCAN_FACTS, workdir=tmp_path))

    def test_takes_no_parameters(self) -> None:
        """Verify an empty set is valid and a parameter is an error."""
        processor = SplitNone()
        expect(processor.validate_params({}) == {})
        with pytest.raises(InvalidParametersError, match=r'split\.none'):
            processor.validate_params({'angle': 1})
        assert_expectations()
