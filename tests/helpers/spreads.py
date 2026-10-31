"""Seeding a book of spreads and running its stages, for the tests that split scans with the real plugins.

The helpers go through the services as a request and a worker would: the recipe of the stage is saved through the
processing service, a run is started, and the jobs of the worker take it from there. What a test reads afterwards it
reads through a catalogue database of its own, as committed.
"""

from typing import TYPE_CHECKING

from bookreviver.domain.recipes import RecipeDraft, Step
from bookreviver.domain.slices import SliceRequest
from bookreviver.domain.stages import PageStageKey
from tests.helpers.builders import open_for
from tests.helpers.samples import png_bytes, spread

if TYPE_CHECKING:
    from bookreviver.domain.accounts import Actor
    from bookreviver.domain.ids import BookId
    from bookreviver.domain.jobs import StageRun
    from bookreviver.domain.pages import Page
    from bookreviver.domain.stages import PageStage, Stage
    from bookreviver.domain.versions import PageVersion
    from tests.helpers.database import LocatedBookDatabases
    from tests.helpers.processing import ProcessingKit

PAGE_WIDTH_PX: int = 900
HEIGHT_PX: int = 1_200
EVERY_PAGE: SliceRequest = SliceRequest(limit=100)
SPLIT_SPREAD: str = 'split.spread'


async def seed_spreads(kit: ProcessingKit, *, count: int = 1) -> tuple[Actor, BookId, list[Page]]:
    """Seed a book with scans of spreads, each shown whole by one page.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param count: How many scans, and so pages, to seed.
    :type count: int
    :returns: The actor, the book and the pages in book order.
    :rtype: tuple[Actor, BookRecord, list[Page]]
    """
    actor, book_id = await kit.seed_book()
    pages = [
        (await kit.seed_scan_page(book_id, order_key=f'a{number}', image=png_bytes(spread(PAGE_WIDTH_PX, HEIGHT_PX))))[
            0
        ]
        for number in range(count)
    ]
    return actor, book_id, pages


async def use_recipe(kit: ProcessingKit, actor: Actor, book_id: BookId, stage: Stage, processor_key: str) -> None:
    """Give the recipe of text pages of a stage one step with default parameters.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param actor: Account owning the book.
    :type actor: Actor
    :param book_id: Identifier of the book the recipe belongs to.
    :type book_id: BookId
    :param stage: The stage.
    :type stage: Stage
    :param processor_key: Key of the processor of the one step.
    :type processor_key: str
    """
    draft = RecipeDraft(steps=[Step(processor_key=processor_key)])
    await kit.edit_recipe(actor, book_id, stage, draft)


async def run_stage(kit: ProcessingKit, actor: Actor, book_id: BookId, run: StageRun) -> None:
    """Start a run of a stage and let the board run it, with the jobs it puts behind it.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param actor: Account owning the book.
    :type actor: Actor
    :param book_id: Identifier of the book to run in.
    :type book_id: BookId
    :param run: The stage, and the recipe and pages to run it on.
    :type run: StageRun
    """
    await kit.service().start_run(open_for(actor, book_id), run.stage, run)
    await kit.work_queue()


async def book_of(book_id: BookId, *, databases: LocatedBookDatabases) -> list[Page]:
    """Read the pages of a book in book order, as committed.

    :param book_id: The identifier of the book.
    :type book_id: BookId
    :param databases: Opens the databases of the books of the test.
    :type databases: LocatedBookDatabases
    :returns: Its pages.
    :rtype: list[Page]
    """
    listed = await databases.open(book_id).read(lambda repos: repos.pages.list_window(EVERY_PAGE))
    return list(listed.items)


async def stage_of(book_id: BookId, page: Page, stage: Stage, *, databases: LocatedBookDatabases) -> PageStage:
    """Read the record of a stage of a page, as committed.

    :param book_id: Identifier of the book of the page.
    :type book_id: BookId
    :param page: The page.
    :type page: Page
    :param stage: The stage.
    :type stage: Stage
    :param databases: Opens the databases of the books of the test.
    :type databases: LocatedBookDatabases
    :returns: The record.
    :rtype: PageStage
    """
    return await databases.open(book_id).read(lambda repos: repos.page_stages.get(PageStageKey(page.id, stage)))


async def head_of(kit: ProcessingKit, book_id: BookId, page: Page, stage: Stage) -> PageVersion:
    """Read the current version of a stage of a page, as committed.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_id: Identifier of the book of the page.
    :type book_id: BookId
    :param page: The page.
    :type page: Page
    :param stage: The stage.
    :type stage: Stage
    :returns: The version the record of the stage names.
    :rtype: PageVersion
    """
    record = await stage_of(book_id, page, stage, databases=kit.databases)
    assert record.head_version_id is not None
    return await kit.stored_version(book_id, record.head_version_id)
