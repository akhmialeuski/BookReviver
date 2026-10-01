"""Tests for the runner of processors: running a step and storing its output as the files of a version."""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.api.routing import IIIF_ROOT
from bookreviver.domain.enums import (
    ColorMode,
    ImagePolicy,
    Rendition,
    Stage,
    VersionScale,
    VersionState,
)
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import Renditions
from bookreviver.plugins.split_none import SplitNone
from bookreviver.ports.processing import StepOutput
from bookreviver.services.base_versions import BaseVersions
from bookreviver.services.steps import StepRun, StepRunner
from tests.helpers.books import IMAGE
from tests.helpers.builders import EPOCH, make_page, make_project, make_scan, make_source, new_account_id
from tests.helpers.fake_processing import PREVIEW_TOKEN, FakeCatalogue, FakeRenditionWriter
from tests.helpers.page_services import INFO_NAME, FakeTiler
from tests.helpers.processors import FakeProcessor

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.adapters.storage import LocalAssetStore
    from bookreviver.domain.entities import PageVersion, Scan

pytestmark = pytest.mark.anyio

MASK_BYTES: bytes = b'mask'


def _scan() -> Scan:
    """Build a scan of a book whose images are cut.

    :returns: The scan, a gray JPEG.
    :rtype: Scan
    """
    source = make_source(project_id=make_project(owner_id=new_account_id()).id)
    scan = make_scan(source=source, number=0)
    return evolve(scan, renditions=Renditions(ready=True))


class Rig:
    """A runner of processors over a local asset store, with the fake writer and tiler that record what they cut.

    :ivar runner: The runner under test.
    :ivar tiler: The fake tiler.
    :ivar writer: The fake writer of renditions.
    :ivar keys: Keys of the project of the scan.
    :ivar scan: A scan whose ``full`` image is not stored yet.
    :ivar version: The pending base version of a page shown from the scan.
    """

    def __init__(self, assets: LocalAssetStore) -> None:
        """Build the runner over the store.

        :param assets: Local asset store of the test.
        :type assets: LocalAssetStore
        """
        self.assets = assets
        self.tiler = FakeTiler()
        self.writer = FakeRenditionWriter()
        self.fake = FakeProcessor()
        self.runner = StepRunner(
            assets=assets,
            catalogue=FakeCatalogue([SplitNone(), self.fake]),
            renditions=self.writer,
            tiler=self.tiler,
            iiif_root=IIIF_ROOT,
        )
        self.scan = _scan()
        self.keys = ProjectKeys(self.scan.project_id)
        page = make_page(project_id=self.scan.project_id, scan=self.scan)
        self.version = BaseVersions.split_none(page=page, scan=self.scan, state=VersionState.PENDING, moment=EPOCH)

    async def store_scan(self) -> None:
        """Store the ``full`` image of the scan."""
        async with self.assets.writable(self.keys.scan_rendition(self.scan, Rendition.FULL_JPEG)) as path:
            path.write_bytes(IMAGE)

    def split_none(self) -> StepRun:
        """Build the run of ``split.none`` on the scan.

        :returns: The run.
        :rtype: StepRun
        """
        return StepRun(
            processor_key='split.none',
            params={},
            input_data=self.scan.facts.as_data(),
            image=self.keys.scan_rendition(self.scan, Rendition.FULL_JPEG),
        )

    async def make(self, version: PageVersion | None = None, *, tiles: bool = True) -> PageVersion:
        """Run ``split.none`` and store its output as the files of a version.

        :param version: The version to make, or None for the rig's pending one.
        :type version: PageVersion | None
        :param tiles: Whether to cut the pyramid too.
        :type tiles: bool
        :returns: The version as ready.
        :rtype: PageVersion
        """
        async with self.runner.execute(self.split_none()) as result:
            return await self.runner.store(
                self.keys,
                version or self.version,
                result.outputs[0],
                policy=ImagePolicy.COMPACT,
                tiles=tiles,
            )


@pytest.fixture
def fx_rig(fx_asset_store: LocalAssetStore) -> Rig:
    """Build the rig over the test's asset store.

    :param fx_asset_store: Local asset store of the test.
    :type fx_asset_store: LocalAssetStore
    :returns: The rig.
    :rtype: Rig
    """
    return Rig(fx_asset_store)


class TestStore:
    """Tests for StepRunner.execute() and StepRunner.store()."""

    async def test_version_holds_its_own_copy_of_the_image_and_its_other_files(
        self, fx_rig: Rig, fx_storage_root: Path
    ) -> None:
        """Verify the version has the image of the scan, a preview, a thumbnail and a pyramid cut from the copy.

        :param fx_rig: The runner and what it works on.
        :type fx_rig: Rig
        :param fx_storage_root: Storage root of the test.
        :type fx_storage_root: Path
        """
        await fx_rig.store_scan()
        ready = await fx_rig.make()
        directory = fx_storage_root / fx_rig.keys.version_directory(ready)
        expect((directory / Rendition.FULL_JPEG).read_bytes() == IMAGE)
        expect((directory / Rendition.PREVIEW).read_bytes() == PREVIEW_TOKEN)
        expect((directory / Rendition.TILES / INFO_NAME).read_text().startswith(IIIF_ROOT))
        expect(all(image == directory / Rendition.FULL_JPEG for image in fx_rig.tiler.cut))
        expect((ready.state, ready.tiles_ready) == (VersionState.READY, True))
        expect(ready.renditions == Renditions(ready=True, full=Rendition.FULL_JPEG))
        assert_expectations()

    async def test_data_and_transform_come_from_the_output(self, fx_rig: Rig) -> None:
        """Verify the version records the size of the scan in its data, as the plugin wrote it.

        :param fx_rig: The runner and what it works on.
        :type fx_rig: Rig
        """
        await fx_rig.store_scan()
        ready = await fx_rig.make()
        facts = fx_rig.scan.facts
        assert dict(ready.data) == {'width_px': facts.width_px, 'height_px': facts.height_px}

    async def test_replaces_what_an_earlier_attempt_left_in_the_directory(
        self, fx_rig: Rig, fx_storage_root: Path
    ) -> None:
        """Verify a second run removes the first one's files first, since a stored file is never replaced.

        :param fx_rig: The runner and what it works on.
        :type fx_rig: Rig
        :param fx_storage_root: Storage root of the test.
        :type fx_storage_root: Path
        """
        await fx_rig.store_scan()
        await fx_rig.make()
        ready = await fx_rig.make()
        assert (fx_storage_root / fx_rig.keys.version_directory(ready) / Rendition.FULL_JPEG).read_bytes() == IMAGE

    async def test_pyramid_is_left_out_when_not_asked_for(self, fx_rig: Rig, fx_storage_root: Path) -> None:
        """Verify a version that is not current gets no pyramid, and cutting it later marks it.

        :param fx_rig: The runner and what it works on.
        :type fx_rig: Rig
        :param fx_storage_root: Storage root of the test.
        :type fx_storage_root: Path
        """
        await fx_rig.store_scan()
        ready = await fx_rig.make(tiles=False)
        directory = fx_storage_root / fx_rig.keys.version_directory(ready)
        cut = await fx_rig.runner.cut_tiles(fx_rig.keys, ready)
        expect((ready.tiles_ready, cut.tiles_ready) == (False, True))
        expect((directory / Rendition.TILES / INFO_NAME).is_file())
        assert_expectations()

    async def test_preview_run_stores_the_preview_file_alone(self, fx_rig: Rig, fx_storage_root: Path) -> None:
        """Verify a version of the preview scale holds ``preview.jpg`` and nothing else.

        :param fx_rig: The runner and what it works on.
        :type fx_rig: Rig
        :param fx_storage_root: Storage root of the test.
        :type fx_storage_root: Path
        """
        await fx_rig.store_scan()
        version = evolve(fx_rig.version, scale=VersionScale.PREVIEW, id=type(fx_rig.version.id)('0123456789abcdef'))
        ready = await fx_rig.make(version)
        directory = fx_storage_root / fx_rig.keys.version_directory(ready)
        expect(sorted(entry.name for entry in directory.iterdir()) == [Rendition.PREVIEW])
        expect(
            (ready.state, ready.tiles_ready, ready.renditions) == (VersionState.READY, False, Renditions(ready=True))
        )
        assert_expectations()

    async def test_step_that_fails_publishes_nothing(self, fx_rig: Rig, fx_storage_root: Path) -> None:
        """Verify an error inside the store leaves no directory, so the version can be made again.

        :param fx_rig: The runner and what it works on.
        :type fx_rig: Rig
        :param fx_storage_root: Storage root of the test.
        :type fx_storage_root: Path
        """
        await fx_rig.store_scan()
        output = StepOutput(image=None, color_mode=ColorMode.GRAY)

        class StoreFailedError(Exception):
            """Raised in the middle of the store."""

        with pytest.raises(StoreFailedError):
            async with fx_rig.assets.writable(fx_rig.keys.version_directory(fx_rig.version)):
                raise StoreFailedError
        await fx_rig.runner.store(fx_rig.keys, fx_rig.version, output, policy=ImagePolicy.COMPACT, tiles=False)
        directory = fx_storage_root / fx_rig.keys.version_directory(fx_rig.version)
        assert directory.is_dir()

    async def test_unknown_processor_is_not_found(self, fx_rig: Rig) -> None:
        """Reject a run of a processor the catalogue does not have.

        :param fx_rig: The runner and what it works on.
        :type fx_rig: Rig
        """
        with pytest.raises(NotFoundError):
            async with fx_rig.runner.execute(StepRun(processor_key='geometry.missing', params={}, input_data={})):
                pass

    async def test_pyramid_of_a_version_without_an_image_is_a_conflict(self, fx_rig: Rig) -> None:
        """Reject cutting the pyramid of a version that has no ready image.

        :param fx_rig: The runner and what it works on.
        :type fx_rig: Rig
        """
        with pytest.raises(ConflictError, match='no image'):
            await fx_rig.runner.cut_tiles(fx_rig.keys, evolve(fx_rig.version, renditions=None, stage=Stage.PAGE_SPLIT))
