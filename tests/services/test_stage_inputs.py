"""Tests for the rule of what a stage reads on a page, which a run and the rows of a stage both follow."""

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import Stage, StageState, VersionState
from bookreviver.domain.values import Renditions
from bookreviver.services.stage_inputs import StageInputs
from tests.helpers.builders import make_page_stage, make_page_version
from tests.helpers.stage_heads import seed_head

if TYPE_CHECKING:
    from bookreviver.domain.entities import Page, PageVersion, Project
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

WINDOW_SIZE: int = 5
FIRST_KEY: str = 'a0'
SECOND_KEY: str = 'a1'
THIRD_KEY: str = 'a2'


async def seed_page(kit: ProcessingKit, project: Project, order_key: str) -> tuple[Page, PageVersion]:
    """Seed a page with its ready base version, which the record of the page split names as current.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param project: Project owning the page.
    :type project: Project
    :param order_key: Order key of the page.
    :type order_key: str
    :returns: The page and its base version.
    :rtype: tuple[Page, PageVersion]
    """
    page, _ = await kit.seed_scan_page(project, order_key=order_key)
    return page, await kit.seed_base_version(page)


async def seed_loose_base(
    kit: ProcessingKit, page: Page, *, minutes: int, state: VersionState = VersionState.READY, ready: bool = True
) -> PageVersion:
    """Commit a base version of a page that no record of a stage names, as a page cut from its scan again has.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param page: The page.
    :type page: Page
    :param minutes: Minutes after the epoch the version was created, which orders the base versions of the page.
    :type minutes: int
    :param state: State of the version.
    :type state: VersionState
    :param ready: Whether the files of the version are published.
    :type ready: bool
    :returns: The version.
    :rtype: PageVersion
    """
    version = evolve(
        make_page_version(page_id=page.id, minutes=minutes), state=state, renditions=Renditions(ready=ready)
    )
    uow = kit.uow()
    await uow.page_versions.add(version)
    await uow.commit()
    return version


class TestOf:
    """Tests for StageInputs.of."""

    async def test_the_nearest_earlier_stage_with_a_head_is_read(self, fx_kit: ProcessingKit) -> None:
        """Verify each stage reads the current version of the stage right before it that has one.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project = await fx_kit.seed_project()
        page, base = await seed_page(fx_kit, project, FIRST_KEY)
        geometry = await seed_head(fx_kit, page, Stage.GEOMETRY, after=base)
        cleanup = await seed_head(fx_kit, page, Stage.CLEANUP, after=geometry)
        inputs = StageInputs(uow=fx_kit.uow())
        expect(await inputs.of([page.id], Stage.GEOMETRY) == {page.id: base})
        expect(await inputs.of([page.id], Stage.CLEANUP) == {page.id: geometry})
        expect(await inputs.of([page.id], Stage.LAYOUT) == {page.id: cleanup})
        assert_expectations()

    async def test_an_earlier_stage_without_a_head_is_skipped_for_the_one_before_it(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a stage that never ran, or has a record with no current version, falls through to the next one back.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project = await fx_kit.seed_project()
        page, base = await seed_page(fx_kit, project, FIRST_KEY)
        geometry = await seed_head(fx_kit, page, Stage.GEOMETRY, after=base)
        uow = fx_kit.uow()
        await uow.page_stages.save(make_page_stage(page_id=page.id, stage=Stage.CLEANUP, head_version_id=None))
        await uow.commit()
        assert await StageInputs(uow=fx_kit.uow()).of([page.id], Stage.LAYOUT) == {page.id: geometry}

    async def test_a_head_whose_files_are_not_ready_is_skipped_like_a_missing_one(self, fx_kit: ProcessingKit) -> None:
        """Verify a stage reads the version before a current version that has no published image.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project = await fx_kit.seed_project()
        page, base = await seed_page(fx_kit, project, FIRST_KEY)
        geometry = await seed_head(fx_kit, page, Stage.GEOMETRY, after=base)
        await seed_head(fx_kit, page, Stage.CLEANUP, after=geometry, ready=False)
        assert await StageInputs(uow=fx_kit.uow()).of([page.id], Stage.LAYOUT) == {page.id: geometry}

    async def test_the_latest_ready_base_version_is_read_when_no_earlier_stage_has_a_head(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a page cut from its scan again is read from its newest base version that is ready.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project = await fx_kit.seed_project()
        page, _ = await fx_kit.seed_scan_page(project, order_key=FIRST_KEY)
        await seed_loose_base(fx_kit, page, minutes=1)
        latest = await seed_loose_base(fx_kit, page, minutes=2)
        await seed_loose_base(fx_kit, page, minutes=3, state=VersionState.PENDING, ready=False)
        assert await StageInputs(uow=fx_kit.uow()).of([page.id], Stage.GEOMETRY) == {page.id: latest}

    async def test_a_page_with_no_image_to_read_has_no_entry(self, fx_kit: ProcessingKit) -> None:
        """Verify a page that has no version, and a base version that is not ready, are left out of the answer.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project = await fx_kit.seed_project()
        empty, _ = await fx_kit.seed_scan_page(project, order_key=FIRST_KEY)
        unready, _ = await fx_kit.seed_scan_page(project, order_key=SECOND_KEY)
        await seed_loose_base(fx_kit, unready, minutes=1, state=VersionState.PENDING, ready=False)
        assert await StageInputs(uow=fx_kit.uow()).of([empty.id, unready.id], Stage.GEOMETRY) == {}

    async def test_the_page_split_reads_the_scan_so_it_has_no_input_version(self, fx_kit: ProcessingKit) -> None:
        """Verify the page split gets nothing, though the page has a base version a later stage would read.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project = await fx_kit.seed_project()
        page, _ = await seed_page(fx_kit, project, FIRST_KEY)
        assert await StageInputs(uow=fx_kit.uow()).of([page.id], Stage.PAGE_SPLIT) == {}

    async def test_a_window_of_pages_is_answered_in_one_call_page_by_page(self, fx_kit: ProcessingKit) -> None:
        """Verify every page of the window gets what it reads, from its own stages and not from a neighbour's.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project = await fx_kit.seed_project()
        with_head, with_head_base = await seed_page(fx_kit, project, FIRST_KEY)
        geometry = await seed_head(fx_kit, with_head, Stage.GEOMETRY, after=with_head_base)
        base_only, base = await seed_page(fx_kit, project, SECOND_KEY)
        empty, _ = await fx_kit.seed_scan_page(project, order_key=THIRD_KEY)
        found = await StageInputs(uow=fx_kit.uow()).of([with_head.id, base_only.id, empty.id], Stage.CLEANUP)
        assert found == {with_head.id: geometry, base_only.id: base}

    async def test_a_window_is_read_with_the_same_queries_whatever_its_size(self, fx_kit: ProcessingKit) -> None:
        """Verify the records, the current versions and the base versions are each read once for the whole window.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project = await fx_kit.seed_project()
        pages = []
        for number in range(WINDOW_SIZE):
            if number >= WINDOW_SIZE - 2:
                # The last pages have a base version no stage names, so the base versions have to be read
                page, _ = await fx_kit.seed_scan_page(project, order_key=f'a{number}')
                await seed_loose_base(fx_kit, page, minutes=number)
            else:
                page, base = await seed_page(fx_kit, project, f'a{number}')
                if number % 2 == 0:
                    await seed_head(fx_kit, page, Stage.GEOMETRY, after=base)
            pages.append(page)
        uow = fx_kit.uow()
        with (
            patch.object(uow.page_stages, 'list_for_pages', wraps=uow.page_stages.list_for_pages) as records,
            patch.object(uow.page_versions, 'list_by_ids', wraps=uow.page_versions.list_by_ids) as versions,
            patch.object(uow.page_versions, 'list_base_versions', wraps=uow.page_versions.list_base_versions) as bases,
        ):
            found = await StageInputs(uow=uow).of([page.id for page in pages], Stage.CLEANUP)
        expect(len(found) == WINDOW_SIZE)
        expect((records.call_count, versions.call_count, bases.call_count) == (1, 1, 1))
        assert_expectations()

    async def test_a_record_of_a_later_stage_is_never_read(self, fx_kit: ProcessingKit) -> None:
        """Verify a stage does not read the current version of a stage after it, whatever the page holds there.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project = await fx_kit.seed_project()
        page, base = await seed_page(fx_kit, project, FIRST_KEY)
        geometry = await seed_head(fx_kit, page, Stage.GEOMETRY, after=base)
        await seed_head(fx_kit, page, Stage.CLEANUP, after=geometry)
        uow = fx_kit.uow()
        await uow.page_stages.save(
            make_page_stage(page_id=page.id, stage=Stage.LAYOUT, head_version_id=geometry.id, state=StageState.STALE)
        )
        await uow.commit()
        assert await StageInputs(uow=fx_kit.uow()).of([page.id], Stage.GEOMETRY) == {page.id: base}
