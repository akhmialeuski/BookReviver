"""Tests for geometry.dewarp with the UVDoc network, which are skipped where the model has not been downloaded.

The model is a download of the processor and is not kept in the repository, so these tests look for it in the models
directory the settings name and skip with the reason when it is not there. A test never downloads it, since a test must
not need the network. The model is downloaded the first time a page is dewarped by the method in an application that has
the network, after which these tests run.
"""

import json
from pathlib import Path

import numpy as np
import pytest
from delayed_assert import assert_expectations, expect
from PIL import Image

from bookreviver.app.settings import ProcessingSettings
from bookreviver.domain.enums import DewarpMethod, VersionData
from bookreviver.ports.processing import Processor, ProcessorSettings
from tests.helpers.samples import CV_MISSING, save
from tests.plugins.runner import run_on
from tests.plugins.synthetic import bend_columns

MODELS_DIR: Path = ProcessingSettings().models_dir.resolve()
GRAPH: Path = MODELS_DIR / 'UVDoc_grid.onnx'
MODEL_MISSING: str = f'The UVDoc model is not downloaded to {MODELS_DIR}.'
CLEAN_PAGE: Path = Path(__file__).parent / 'data' / 'book_clean_page.jpg'
# The depth of the bend the page is given, in pixels for each thousand of its width
BEND_PX: float = 30.0
GRID_SHAPE: tuple[int, int, int] = (45, 31, 2)
PAGE_NAME: str = 'page.png'
ENCODING: str = 'utf-8'
# The parameters of the method, whose least bend is zero so that a page is remapped whatever the network found
UVDOC_PARAMS: dict[str, object] = {'method': DewarpMethod.UVDOC, 'min_bend': 0}

pytestmark = pytest.mark.skipif(not GRAPH.is_file(), reason=MODEL_MISSING)


@pytest.fixture
def fx_uvdoc() -> Processor:
    """Build the dewarping processor with the models directory of the settings, or skip where OpenCV is missing.

    :returns: The processor ``geometry.dewarp``.
    :rtype: Processor
    """
    processor = pytest.importorskip('bookreviver.plugins.dewarp', reason=CV_MISSING).Dewarp()
    assert isinstance(processor, Processor)
    processor.configure(ProcessorSettings(models_dir=MODELS_DIR))
    return processor


class TestDewarpUvDoc:
    """Tests for Dewarp with the method uvdoc."""

    def test_a_bent_page_of_a_book_is_remapped_by_the_network(self, fx_uvdoc: Processor, tmp_path: Path) -> None:
        """Verify the network gives a grid, the page keeps its size, and the mesh file holds the 45 by 31 grid.

        The least bend is zero so that the page is remapped whatever the network found, since how straight a network
        makes a page is a matter of the network and not of this step.

        :param fx_uvdoc: The processor under test.
        :type fx_uvdoc: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        with Image.open(CLEAN_PAGE) as page:
            bent, _ = bend_columns(np.asarray(page.convert('L'), dtype=np.uint8), BEND_PX)
        path = save(Image.fromarray(bent), tmp_path / PAGE_NAME)
        output = run_on(fx_uvdoc, path, tmp_path, UVDOC_PARAMS)
        assert output.image is not None
        assert output.mesh is not None
        with Image.open(output.image) as result:
            expect(result.size == (bent.shape[1], bent.shape[0]))
        grid = np.array(json.loads(output.mesh.read_text(encoding=ENCODING))['grid'])
        expect(grid.shape == GRID_SHAPE)
        expect(output.data[VersionData.SKIPPED] is False)
        expect(output.data[VersionData.BEND] >= 0)
        # The network follows no lines, so it has nothing to count and no residual to tell
        expect(VersionData.RESIDUAL not in output.data)
        expect(VersionData.MESH not in output.data)
        assert_expectations()

    def test_a_second_page_is_dewarped_by_the_network_that_is_loaded_already(
        self, fx_uvdoc: Processor, tmp_path: Path
    ) -> None:
        """Verify a second page gives the same grid for the same picture, with no download and no second load.

        :param fx_uvdoc: The processor under test.
        :type fx_uvdoc: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        with Image.open(CLEAN_PAGE) as page:
            path = save(page.convert('L'), tmp_path / PAGE_NAME)
        first = run_on(fx_uvdoc, path, tmp_path, UVDOC_PARAMS)
        second = run_on(fx_uvdoc, path, tmp_path, UVDOC_PARAMS)
        assert first.mesh is not None
        assert second.mesh is not None
        assert first.mesh.read_text(encoding=ENCODING) == second.mesh.read_text(encoding=ENCODING)
