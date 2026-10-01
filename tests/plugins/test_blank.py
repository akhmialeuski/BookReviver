"""Tests for the pages.blank processor."""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect
from PIL import Image

from bookreviver.domain.enums import ColorMode, TransformKind
from bookreviver.domain.errors import InvalidParametersError
from bookreviver.plugins.blank import BlankPage
from bookreviver.ports.processing import StepInput

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.domain.values import MetadataMap


class TestBlankPage:
    """Tests for BlankPage."""

    def test_draws_a_white_one_bit_page_of_the_size_asked(self, tmp_path: Path) -> None:
        """Verify the leaf is a 1-bit PNG of exact size and only white pixels, with its size in the data.

        :param tmp_path: Temporary directory of the test, holding the work directory.
        :type tmp_path: Path
        """
        processor = BlankPage()
        params = processor.validate_params({'width_px': 120, 'height_px': 90, 'dpi': 300})
        result = processor.run(StepInput(image=None, params=params, workdir=tmp_path))
        [output] = result.outputs
        assert output.image is not None
        with Image.open(output.image) as leaf:
            expect((leaf.format, leaf.mode, leaf.size) == ('PNG', '1', (120, 90)))
            expect(set(leaf.convert('L').get_flattened_data()) == {255})
        expect(output.color_mode is ColorMode.BILEVEL)
        expect(output.transform.kind is TransformKind.IDENTITY)
        expect(output.data == {'width_px': 120, 'height_px': 90, 'dpi': 300})
        assert_expectations()

    def test_resolution_is_left_out_of_the_data_when_unknown(self, tmp_path: Path) -> None:
        """Verify a leaf made without a resolution says none in its data.

        :param tmp_path: Temporary directory of the test, holding the work directory.
        :type tmp_path: Path
        """
        processor = BlankPage()
        params = processor.validate_params({'width_px': 10, 'height_px': 10})
        [output] = processor.run(StepInput(image=None, params=params, workdir=tmp_path)).outputs
        assert output.data == {'width_px': 10, 'height_px': 10}

    @pytest.mark.parametrize(
        'raw',
        [
            {},
            {'width_px': 10},
            {'width_px': 0, 'height_px': 10},
            {'width_px': 10, 'height_px': 10, 'dpi': -1},
            {'width_px': 1, 'height_px': 1, 'x': 1},
        ],
        ids=['empty', 'no-height', 'zero-width', 'negative-dpi', 'unknown'],
    )
    def test_parameters_that_do_not_fit_the_schema_are_rejected(self, raw: MetadataMap) -> None:
        """Reject parameters that miss a size, hold an impossible one or name a parameter the leaf does not have.

        :param raw: Parameters under test.
        :type raw: MetadataMap
        """
        with pytest.raises(InvalidParametersError, match=r'pages\.blank'):
            BlankPage().validate_params(raw)

    def test_spec_carries_the_json_schema_of_the_parameters(self) -> None:
        """Verify the interface can draw the form from the spec."""
        schema = BlankPage.spec.parameters
        assert set(schema['properties']) == {'width_px', 'height_px', 'dpi'}
