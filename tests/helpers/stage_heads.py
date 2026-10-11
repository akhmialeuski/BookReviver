"""Seeding the current version of a stage of a page directly, for the tests that read what a stage reads on a page."""

from typing import TYPE_CHECKING

from attrs import evolve

from bookreviver.domain.enums import StageState, VersionState
from bookreviver.domain.values import Renditions
from tests.helpers.builders import make_page_stage, make_page_version

if TYPE_CHECKING:
    from bookreviver.domain.entities import Page, PageVersion
    from bookreviver.domain.enums import Stage
    from tests.helpers.processing import ProcessingKit


async def seed_head(
    kit: ProcessingKit, page: Page, stage: Stage, *, after: PageVersion, ready: bool = True
) -> PageVersion:
    """Commit a version of a stage that reads another version, and the record of the stage that names it as current.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param page: The page.
    :type page: Page
    :param stage: The stage the version and the record belong to.
    :type stage: Stage
    :param after: The version the new one reads, which an earlier stage made.
    :type after: PageVersion
    :param ready: Whether the files of the new version are published.
    :type ready: bool
    :returns: The version, which is the current one of the stage.
    :rtype: PageVersion
    """
    version = evolve(
        make_page_version(page_id=page.id),
        stage=stage,
        input_id=after.id,
        renditions=Renditions(ready=ready),
        state=VersionState.READY,
    )
    uow = kit.uow()
    async with uow.change_book(page.project_id):
        await uow.page_versions.add(version)
        await uow.page_stages.save(
            make_page_stage(page_id=page.id, stage=stage, head_version_id=version.id, state=StageState.FRESH)
        )
    return version
