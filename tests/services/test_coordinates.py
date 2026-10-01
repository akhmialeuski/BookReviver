"""Tests of the chain of transforms from a scan to the page versions made from it, with the real OpenCV plugins.

A landmark is drawn on a generated scan, the scan is split and deskewed, the landmark is found again on the image of a
version, and the transforms of the chain have to give its place in the scan to within a pixel. The tests need OpenCV,
and are skipped with the reason where the optional group ``cv`` is not installed.
"""

import io
from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect
from PIL import Image

from bookreviver.domain.enums import Stage
from bookreviver.domain.geometry import Point, Rotation
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import NewPageEdit, PageEditKey, StageRun
from tests.helpers.samples import find_mark, mark, png_bytes, spread
from tests.helpers.spreads import (
    HEIGHT_PX,
    PAGE_WIDTH_PX,
    SPLIT_SPREAD,
    book_of,
    head_of,
    run_stage,
    use_recipe,
)

if TYPE_CHECKING:
    from bookreviver.domain.entities import Actor, Page, PageVersion, Project
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

DESKEW: str = 'geometry.deskew'
# The angle the left page of the scan is turned by, and how far a point may be from where the chain puts it, in pixels
TILT_DEGREES: float = 2.0
TOLERANCE_PX: float = 1.0
MARK_ON_LEFT: tuple[int, int] = (300, 500)
MARK_ON_RIGHT: tuple[int, int] = (1_300, 700)


async def landmark_on(kit: ProcessingKit, project: Project, version: PageVersion) -> Point:
    """Find the landmark on the stored image of a version.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param project: Project owning the version's page.
    :type project: Project
    :param version: A ready version that has its files.
    :type version: PageVersion
    :returns: The centre of the landmark in the pixels of the image of the version.
    :rtype: Point
    """
    assert version.renditions is not None
    async with kit.assets.readable(ProjectKeys(project.id).version_rendition(version, version.renditions.full)) as full:
        with Image.open(io.BytesIO(full.read_bytes())) as image:
            x, y = find_mark(image)
    return Point(x=x, y=y)


async def split_marked_spread(kit: ProcessingKit) -> tuple[Actor, Project, Page, Page]:
    """Split a generated spread with a landmark on each half, whose left page is skewed.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The actor, the project, and the left and the right page.
    :rtype: tuple[Actor, Project, Page, Page]
    """
    scan = spread(PAGE_WIDTH_PX, HEIGHT_PX, tilt=TILT_DEGREES)
    mark(scan, MARK_ON_LEFT)
    mark(scan, MARK_ON_RIGHT)
    actor, project = await kit.seed_project()
    await kit.seed_scan_page(project, image=png_bytes(scan))
    await use_recipe(kit, actor, project, Stage.PAGE_SPLIT, SPLIT_SPREAD)
    await run_stage(kit, actor, project, StageRun(stage=Stage.PAGE_SPLIT))
    left, right = await book_of(kit, project)
    return actor, project, left, right


class TestChainOfTransforms:
    """Tests for mapping the points of a version back to the scan."""

    @pytest.mark.parametrize('degrees', [-TILT_DEGREES, None], ids=['rotation-edit', 'search'])
    async def test_point_of_the_deskewed_left_half_maps_back_to_the_same_point_of_the_scan(
        self, fx_cv_kit: ProcessingKit, degrees: float | None
    ) -> None:
        """Verify the landmark on the output of ``geometry.deskew`` is mapped to its own place in the scan.

        The chain is two transforms: the rotation of the deskew and the crop of the split. It holds whether the angle
        is the one the user gave or the one the search found, since the transform is the matrix the image was turned by.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        :param degrees: The angle of the rotation edit of the user, or None to leave the angle to the search.
        :type degrees: float | None
        """
        actor, project, left, _right = await split_marked_spread(fx_cv_kit)
        await use_recipe(fx_cv_kit, actor, project, Stage.GEOMETRY, DESKEW)
        if degrees is not None:
            edit = NewPageEdit(kind=Rotation.editor, geometry=Rotation(degrees=degrees))
            await fx_cv_kit.edits().save(actor, project.id, PageEditKey(left.id, Stage.GEOMETRY, DESKEW), edit, None)
        await run_stage(fx_cv_kit, actor, project, StageRun(stage=Stage.GEOMETRY, page_ids=(left.id,)))
        deskewed = await head_of(fx_cv_kit, left, Stage.GEOMETRY)
        found = await landmark_on(fx_cv_kit, project, deskewed)
        [mapped] = await fx_cv_kit.service().map_to_scan(actor, project.id, left.id, deskewed.id, [found])
        expect(deskewed.input_id is not None)
        expect(abs(mapped.x - MARK_ON_LEFT[0]) <= TOLERANCE_PX)
        expect(abs(mapped.y - MARK_ON_LEFT[1]) <= TOLERANCE_PX)
        assert_expectations()

    async def test_point_of_the_right_half_maps_back_through_its_crop(self, fx_cv_kit: ProcessingKit) -> None:
        """Verify the landmark on the right half is mapped to the scan by the crop alone.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, project, _left, right = await split_marked_spread(fx_cv_kit)
        base = await head_of(fx_cv_kit, right, Stage.PAGE_SPLIT)
        found = await landmark_on(fx_cv_kit, project, base)
        [mapped] = await fx_cv_kit.service().map_to_scan(actor, project.id, right.id, base.id, [found])
        expect(abs(mapped.x - MARK_ON_RIGHT[0]) <= TOLERANCE_PX)
        expect(abs(mapped.y - MARK_ON_RIGHT[1]) <= TOLERANCE_PX)
        assert_expectations()
