"""Tests for the base versions of pages: how they are built, and how a scan is copied into one."""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.api.routing import IIIF_ROOT
from bookreviver.domain.enums import ColorMode, Rendition, Stage, VersionState
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import PageSize, Renditions
from bookreviver.services.base_versions import PAGES_BLANK, SPLIT_NONE, BaseVersions
from tests.helpers.books import IMAGE
from tests.helpers.builders import EPOCH, make_page, make_project, make_scan, make_source, new_account_id
from tests.helpers.page_services import INFO_NAME, FakeTiler

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.adapters.storage import LocalAssetStore
    from bookreviver.domain.entities import Scan

pytestmark = pytest.mark.anyio


def _cut_scan(*, dpi_x: float | None = None, dpi_y: float | None = None, full: Rendition = Rendition.FULL_JPEG) -> Scan:
    """Build a scan of a book whose images are cut, in a given format.

    :param dpi_x: Horizontal resolution of the scan, or None.
    :type dpi_x: float | None
    :param dpi_y: Vertical resolution of the scan, or None.
    :type dpi_y: float | None
    :param full: Format of the scan's full image.
    :type full: Rendition
    :returns: The scan.
    :rtype: Scan
    """
    source = make_source(project_id=make_project(owner_id=new_account_id()).id)
    scan = make_scan(source=source, number=0)
    facts = evolve(scan.facts, width_px=640, height_px=480, color_mode=ColorMode.GRAY, dpi_x=dpi_x, dpi_y=dpi_y)
    return evolve(scan, facts=facts, renditions=Renditions(ready=True, full=full))


class TestSplitNone:
    """Tests for BaseVersions.split_none()."""

    @pytest.mark.parametrize('state', [VersionState.PENDING, VersionState.READY])
    def test_builds_the_version_of_a_whole_scan_in_the_scans_format(self, state: VersionState) -> None:
        """Verify the stage, the step, the format, the readiness of the renditions and the state follow the arguments.

        :param state: State the version is built in.
        :type state: VersionState
        """
        scan = _cut_scan(full=Rendition.FULL_PNG)
        page = make_page(project_id=scan.project_id, scan=scan)

        version = BaseVersions.split_none(page=page, scan=scan, state=state, moment=EPOCH)

        expect((version.stage, version.processor, version.state) == (Stage.PAGE_SPLIT, SPLIT_NONE, state))
        expect(version.renditions == Renditions(ready=state is VersionState.READY, full=Rendition.FULL_PNG))
        expect(version.id == version.identify(page_id=page.id, processor=SPLIT_NONE))
        assert_expectations()

    @pytest.mark.parametrize(
        ('dpi_x', 'dpi_y', 'expected'), [(300.0, 200.0, 300.0), (None, 200.0, 200.0), (None, None, None)]
    )
    def test_records_the_size_and_the_finer_resolution_of_the_scan(
        self, dpi_x: float | None, dpi_y: float | None, expected: float | None
    ) -> None:
        """Verify the data holds the pixel size of the scan and the greater of its resolutions, when it has one.

        :param dpi_x: Horizontal resolution of the scan.
        :type dpi_x: float | None
        :param dpi_y: Vertical resolution of the scan.
        :type dpi_y: float | None
        :param expected: Resolution the data records.
        :type expected: float | None
        """
        scan = _cut_scan(dpi_x=dpi_x, dpi_y=dpi_y)
        page = make_page(project_id=scan.project_id, scan=scan)

        version = BaseVersions.split_none(page=page, scan=scan, state=VersionState.READY, moment=EPOCH)

        assert PageSize.from_data(version.data) == PageSize(width_px=640, height_px=480, dpi=expected)


class TestBlank:
    """Tests for BaseVersions.blank()."""

    def test_builds_a_pending_version_of_the_page_order_stage_with_the_size_in_its_data(self) -> None:
        """Verify a blank leaf's version is pending, in the page order stage, in the format given."""
        page = make_page(project_id=make_project(owner_id=new_account_id()).id)
        size = PageSize(width_px=100, height_px=150, dpi=96.0)

        version = BaseVersions.blank(page=page, size=size, full=Rendition.FULL_PNG, moment=EPOCH)

        expect(
            (version.stage, version.processor, version.state) == (Stage.PAGE_ORDER, PAGES_BLANK, VersionState.PENDING)
        )
        expect(version.renditions == Renditions(ready=False, full=Rendition.FULL_PNG))
        expect(PageSize.from_data(version.data) == size)
        assert_expectations()


class TestCopyScan:
    """Tests for BaseVersions.copy_scan()."""

    async def test_copies_the_full_image_and_cuts_the_other_renditions_from_the_copy(
        self, fx_asset_store: LocalAssetStore, fx_storage_root: Path
    ) -> None:
        """Verify the version holds its own copy of the scan's image, and its preview, thumbnail and pyramid.

        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_storage_root: Storage root of the test.
        :type fx_storage_root: Path
        """
        scan = _cut_scan()
        page = make_page(project_id=scan.project_id, scan=scan)
        version = BaseVersions.split_none(page=page, scan=scan, state=VersionState.PENDING, moment=EPOCH)
        keys = ProjectKeys(scan.project_id)
        async with fx_asset_store.writable(keys.scan_rendition(scan, Rendition.FULL_JPEG)) as path:
            path.write_bytes(IMAGE)
        tiler = FakeTiler()

        await BaseVersions(assets=fx_asset_store, tiler=tiler, iiif_root=IIIF_ROOT).copy_scan(version, scan)

        directory = fx_storage_root / keys.version_directory(version)
        expect((directory / Rendition.FULL_JPEG).read_bytes() == IMAGE)
        expect((directory / Rendition.PREVIEW).is_file() and (directory / Rendition.THUMBNAIL).is_file())
        expect((directory / Rendition.TILES / INFO_NAME).read_text().startswith(IIIF_ROOT))
        expect(all(image == directory / Rendition.FULL_JPEG for image in tiler.cut))
        assert_expectations()

    async def test_replaces_what_an_earlier_attempt_left_in_the_directory(
        self, fx_asset_store: LocalAssetStore, fx_storage_root: Path
    ) -> None:
        """Verify a second copy removes the first one's files first, since a stored file is never replaced.

        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_storage_root: Storage root of the test.
        :type fx_storage_root: Path
        """
        scan = _cut_scan()
        page = make_page(project_id=scan.project_id, scan=scan)
        version = BaseVersions.split_none(page=page, scan=scan, state=VersionState.PENDING, moment=EPOCH)
        keys = ProjectKeys(scan.project_id)
        async with fx_asset_store.writable(keys.scan_rendition(scan, Rendition.FULL_JPEG)) as path:
            path.write_bytes(IMAGE)
        versions = BaseVersions(assets=fx_asset_store, tiler=FakeTiler(), iiif_root=IIIF_ROOT)

        await versions.copy_scan(version, scan)
        await versions.copy_scan(version, scan)

        assert (fx_storage_root / keys.version_directory(version) / Rendition.FULL_JPEG).read_bytes() == IMAGE
