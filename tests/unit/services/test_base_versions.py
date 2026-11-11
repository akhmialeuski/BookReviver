"""Tests for the base versions of pages: how their rows are built."""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.ids import PageVersionId
from bookreviver.domain.images import ColorMode, Rendition, Renditions
from bookreviver.domain.stages import Stage
from bookreviver.domain.step_settings import BlankParam, PaperFill
from bookreviver.domain.versions import PageSize, VersionInputs, VersionState
from tests.harness.builders.domain import EPOCH, PAGES_BLANK, SPLIT_NONE, make_page, make_scan, make_source
from tests.harness.builders.page_services import make_base_versions

if TYPE_CHECKING:
    from bookreviver.domain.sources import Scan

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
    source = make_source()
    scan = make_scan(source=source, number=0)
    facts = evolve(scan.facts, width_px=640, height_px=480, color_mode=ColorMode.GRAY, dpi_x=dpi_x, dpi_y=dpi_y)
    return evolve(scan, facts=facts, renditions=Renditions(ready=True, full=full))


class TestSplitNone:
    """Tests for make_base_versions().split_none()."""

    @pytest.mark.parametrize('state', [VersionState.PENDING, VersionState.READY])
    def test_builds_the_version_of_a_whole_scan_in_the_scans_format(self, state: VersionState) -> None:
        """Verify the stage, the step, the format, the readiness of the renditions and the state follow the arguments.

        :param state: State the version is built in.
        :type state: VersionState
        """
        scan = _cut_scan(full=Rendition.FULL_PNG)
        page = make_page(scan=scan)

        version = make_base_versions().split_none(page=page, scan=scan, state=state, moment=EPOCH)

        expect((version.stage, version.processor, version.state) == (Stage.PAGE_SPLIT, SPLIT_NONE, state))
        expect(version.renditions == Renditions(ready=state is VersionState.READY, full=Rendition.FULL_PNG))
        expect(version.id == VersionInputs(page_id=page.id, processor=SPLIT_NONE).identify())
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
        page = make_page(scan=scan)

        version = make_base_versions().split_none(page=page, scan=scan, state=VersionState.READY, moment=EPOCH)

        assert PageSize.from_data(version.data) == PageSize(width_px=640, height_px=480, dpi=expected)


class TestBlank:
    """Tests for make_base_versions().blank()."""

    def test_builds_a_pending_version_of_the_page_order_stage_with_the_size_in_its_data(self) -> None:
        """Verify a blank leaf's version is pending, in the page order stage, in the format given."""
        page = make_page()
        size = PageSize(width_px=100, height_px=150, dpi=96.0)

        version = make_base_versions().blank(page=page, size=size, full=Rendition.FULL_PNG, moment=EPOCH)

        expect(
            (version.stage, version.processor, version.state) == (Stage.PAGE_ORDER, PAGES_BLANK, VersionState.PENDING)
        )
        expect(version.renditions == Renditions(ready=False, full=Rendition.FULL_PNG))
        expect(PageSize.from_data(version.data) == size)
        assert_expectations()

    def test_a_white_leaf_has_the_size_alone_for_its_parameters(self) -> None:
        """Verify a leaf without a paper keeps the parameters it had before leaves could have one."""
        page = make_page()
        size = PageSize(width_px=100, height_px=150, dpi=96.0)

        version = make_base_versions().blank(page=page, size=size, full=Rendition.FULL_PNG, moment=EPOCH)

        assert version.params == size.as_data()

    def test_a_leaf_of_the_paper_names_the_pages_it_takes_the_paper_from_in_a_fixed_order(self) -> None:
        """Verify the identifier does not depend on the order the pages are given in, and differs from a white leaf's."""
        page = make_page()
        size = PageSize(width_px=100, height_px=150)
        pages = [PageVersionId('b' * 16), PageVersionId('a' * 16)]

        paper = make_base_versions().blank(
            page=page, size=size, full=Rendition.FULL_PNG, moment=EPOCH, paper_from=pages
        )
        again = make_base_versions().blank(
            page=page, size=size, full=Rendition.FULL_PNG, moment=EPOCH, paper_from=list(reversed(pages))
        )
        white = make_base_versions().blank(page=page, size=size, full=Rendition.FULL_PNG, moment=EPOCH)
        other = make_base_versions().blank(
            page=page, size=size, full=Rendition.FULL_PNG, moment=EPOCH, paper_from=[PageVersionId('c' * 16)]
        )

        expect(paper.params[BlankParam.FILL] == PaperFill.PAPER)
        expect(paper.params[BlankParam.PAPER_FROM] == sorted(pages))
        expect(paper.id == again.id)
        expect(paper.id not in {white.id, other.id} and white.id != other.id)
        assert_expectations()


class TestProcessorsOfBaseVersions:
    """Tests for the processors a base version is made by, which the catalogue names by their roles."""

    def test_the_processors_are_those_the_catalogue_lists_for_the_roles(self) -> None:
        """Verify the builder takes the key and the version of each processor from its spec, and prepares both."""
        base_versions = make_base_versions()
        expect(base_versions.scan_copy.ref == SPLIT_NONE)
        expect(base_versions.blank_leaf.ref == PAGES_BLANK)
        expect(base_versions.prepared_keys == {SPLIT_NONE.key, PAGES_BLANK.key})
        assert_expectations()
