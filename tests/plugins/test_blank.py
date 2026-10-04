"""Tests for the pages.blank processor."""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect
from PIL import Image

from bookreviver.domain.enums import ColorMode, PaperFill, TransformKind
from bookreviver.domain.errors import InvalidParametersError
from bookreviver.plugins.blank import BlankPage, median_paper
from bookreviver.ports.processing import StepInput
from tests.helpers.samples import pixel, ruled_page, same_colour

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.domain.values import MetadataMap


PAPER_COLOUR: tuple[int, int, int] = (205, 185, 140)
INK: tuple[int, int, int] = (25, 20, 20)
PAGE_PX: tuple[int, int] = (120, 160)
RGB: str = 'RGB'


def write_page(path: Path, paper: tuple[int, ...], *, ink: tuple[int, ...] | None = INK) -> Path:
    """Write a page of lines of ink on paper, as a PNG.

    :param path: Where to write.
    :type path: Path
    :param paper: Colour of the paper, one tone for a gray page and three for a colour one.
    :type paper: tuple[int, ...]
    :param ink: Colour of the lines, or None for a page with nothing to part from its paper.
    :type ink: tuple[int, ...] | None
    :returns: The path written.
    :rtype: Path
    """
    ruled_page(PAGE_PX, paper, ink).save(path)
    return path


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
        assert set(schema['properties']) == {'width_px', 'height_px', 'dpi', 'fill', 'paper_from'}

    def test_a_leaf_is_white_unless_the_fill_says_otherwise(self) -> None:
        """Verify the fill is white by default, so the parameters of a leaf made before it existed still fit."""
        params = BlankPage().validate_params({'width_px': 10, 'height_px': 10})
        assert params['fill'] == PaperFill.WHITE

    def test_draws_a_leaf_of_the_colour_of_the_paper_of_the_pages_it_is_given(self, tmp_path: Path) -> None:
        """Verify a leaf of the paper is a colour PNG of the size asked whose every pixel is the paper of the pages.

        :param tmp_path: Temporary directory of the test, holding the work directory and the pages.
        :type tmp_path: Path
        """
        processor = BlankPage()
        params = processor.validate_params({'width_px': 80, 'height_px': 60, 'dpi': 200, 'fill': 'paper'})
        references = [write_page(tmp_path / f'page-{number}.png', PAPER_COLOUR) for number in range(3)]
        step = StepInput(image=None, params=params, workdir=tmp_path, references=references)

        [output] = processor.run(step).outputs

        assert output.image is not None
        with Image.open(output.image) as leaf:
            expect((leaf.mode, leaf.size) == (RGB, (80, 60)))
            expect(same_colour(pixel(leaf, (0, 0)), PAPER_COLOUR))
            expect(same_colour(pixel(leaf, (79, 59)), PAPER_COLOUR))
        expect(output.color_mode is ColorMode.COLOR)
        expect(output.data == {'width_px': 80, 'height_px': 60, 'dpi': 200, 'color_mode': 'color'})
        assert_expectations()

    def test_a_leaf_of_gray_paper_is_a_gray_page(self, tmp_path: Path) -> None:
        """Verify the paper of gray pages gives a one-band gray leaf, not a colour one.

        :param tmp_path: Temporary directory of the test, holding the work directory and the pages.
        :type tmp_path: Path
        """
        processor = BlankPage()
        params = processor.validate_params({'width_px': 30, 'height_px': 20, 'fill': 'paper'})
        page = write_page(tmp_path / 'gray.png', (190,), ink=(30,))

        [output] = processor.run(StepInput(image=None, params=params, workdir=tmp_path, references=[page])).outputs

        assert output.image is not None
        with Image.open(output.image) as leaf:
            expect(leaf.mode == 'L')
            expect(same_colour(pixel(leaf, (5, 5)), (190,)))
        expect(output.color_mode is ColorMode.GRAY)
        assert_expectations()

    def test_the_paper_of_several_pages_is_the_median_of_their_papers(self, tmp_path: Path) -> None:
        """Verify one yellowed page among three does not tint the leaf, which takes the median of the papers.

        :param tmp_path: Temporary directory of the test, holding the pages.
        :type tmp_path: Path
        """
        dark = (150, 120, 60)
        pages = [
            write_page(tmp_path / 'a.png', PAPER_COLOUR),
            write_page(tmp_path / 'b.png', PAPER_COLOUR),
            write_page(tmp_path / 'c.png', dark),
        ]

        found = median_paper(pages)

        assert found is not None
        assert same_colour(found, PAPER_COLOUR)

    @pytest.mark.parametrize(
        'pages',
        [[], [(255, 255, 255)], [None]],
        ids=['no-pages', 'white-paper', 'page-without-ink'],
    )
    def test_a_leaf_with_no_coloured_paper_to_take_is_white(
        self, tmp_path: Path, pages: list[tuple[int, int, int] | None]
    ) -> None:
        """Verify the leaf is the white bilevel page when no page is given, or the paper is white, or has no ink.

        :param tmp_path: Temporary directory of the test, holding the work directory and the pages.
        :type tmp_path: Path
        :param pages: Paper of each page given as a reference, or None for a page of one tone with no ink to measure.
        :type pages: list[tuple[int, int, int] | None]
        """
        processor = BlankPage()
        params = processor.validate_params({'width_px': 16, 'height_px': 16, 'fill': 'paper'})
        references = [
            write_page(tmp_path / f'page-{number}.png', paper or PAPER_COLOUR, ink=None if paper is None else INK)
            for number, paper in enumerate(pages)
        ]

        [output] = processor.run(StepInput(image=None, params=params, workdir=tmp_path, references=references)).outputs

        assert output.image is not None
        with Image.open(output.image) as leaf:
            expect((leaf.mode, set(leaf.convert('L').get_flattened_data())) == ('1', {255}))
        expect(output.color_mode is ColorMode.BILEVEL)
        expect(output.data == {'width_px': 16, 'height_px': 16})
        assert_expectations()

    def test_the_references_are_not_read_by_a_white_leaf(self, tmp_path: Path) -> None:
        """Verify a white leaf ignores the pages it is given, even a coloured one.

        :param tmp_path: Temporary directory of the test, holding the work directory and the page.
        :type tmp_path: Path
        """
        processor = BlankPage()
        params = processor.validate_params({'width_px': 8, 'height_px': 8})
        page = write_page(tmp_path / 'page.png', PAPER_COLOUR)

        [output] = processor.run(StepInput(image=None, params=params, workdir=tmp_path, references=[page])).outputs

        assert output.color_mode is ColorMode.BILEVEL
