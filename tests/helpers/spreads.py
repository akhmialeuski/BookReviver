"""Seeding a book of spreads and running its stages, for the tests that split scans with the real plugins.

The helpers go through the services as a request and a worker would: the recipe of the stage is saved through the
processing service, a run is started, and the jobs of the worker take it from there. What a test reads afterwards it
reads through a unit of work of its own, as committed.
"""

from typing import TYPE_CHECKING

from bookreviver.domain.values import PageStageKey, RecipeDraft, SliceRequest, Step
from tests.helpers.samples import png_bytes, spread

if TYPE_CHECKING:
    from bookreviver.domain.entities import Actor, Page, PageStage, PageVersion, Project
    from bookreviver.domain.enums import Stage
    from bookreviver.domain.values import StageRun
    from tests.helpers.processing import ProcessingKit

PAGE_WIDTH_PX: int = 900
HEIGHT_PX: int = 1_200
EVERY_PAGE: SliceRequest = SliceRequest(limit=100)
SPLIT_SPREAD: str = 'split.spread'


async def seed_spreads(kit: ProcessingKit, *, count: int = 1) -> tuple[Actor, Project, list[Page]]:
    """Seed a project with scans of spreads, each shown whole by one page.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param count: How many scans, and so pages, to seed.
    :type count: int
    :returns: The actor, the project and the pages in book order.
    :rtype: tuple[Actor, Project, list[Page]]
    """
    actor, project = await kit.seed_project()
    pages = [
        (await kit.seed_scan_page(project, order_key=f'a{number}', image=png_bytes(spread(PAGE_WIDTH_PX, HEIGHT_PX))))[
            0
        ]
        for number in range(count)
    ]
    return actor, project, pages


async def use_recipe(kit: ProcessingKit, actor: Actor, project: Project, stage: Stage, processor_key: str) -> None:
    """Make a one-step recipe with default parameters the active recipe of a stage.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param actor: Account owning the project.
    :type actor: Actor
    :param project: Project the recipe belongs to.
    :type project: Project
    :param stage: The stage.
    :type stage: Stage
    :param processor_key: Key of the processor of the one step.
    :type processor_key: str
    """
    draft = RecipeDraft(name=processor_key, steps=[Step(processor_key=processor_key)])
    await kit.service().save_recipe(actor, project.id, stage, draft)


async def run_stage(kit: ProcessingKit, actor: Actor, project: Project, run: StageRun) -> None:
    """Start a run of a stage and let the worker do it.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param actor: Account owning the project.
    :type actor: Actor
    :param project: Project to run in.
    :type project: Project
    :param run: The stage, and the recipe and pages to run it on.
    :type run: StageRun
    """
    job = await kit.service().start_run(actor, project.id, run.stage, run)
    await kit.jobs().run_stage(job.id)
    await kit.work_queue()


async def book_of(kit: ProcessingKit, project: Project) -> list[Page]:
    """Read the pages of a project in book order, as committed.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param project: The project.
    :type project: Project
    :returns: Its pages.
    :rtype: list[Page]
    """
    return list((await kit.uow().pages.list_for_project(project.id, EVERY_PAGE)).items)


async def stage_of(kit: ProcessingKit, page: Page, stage: Stage) -> PageStage:
    """Read the record of a stage of a page, as committed.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param page: The page.
    :type page: Page
    :param stage: The stage.
    :type stage: Stage
    :returns: The record.
    :rtype: PageStage
    """
    return await kit.uow().page_stages.get(PageStageKey(page.id, stage))


async def head_of(kit: ProcessingKit, page: Page, stage: Stage) -> PageVersion:
    """Read the current version of a stage of a page, as committed.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param page: The page.
    :type page: Page
    :param stage: The stage.
    :type stage: Stage
    :returns: The version the record of the stage names.
    :rtype: PageVersion
    """
    record = await stage_of(kit, page, stage)
    assert record.head_version_id is not None
    return await kit.uow().page_versions.get(record.head_version_id)
