"""The repository port of the records of the stages of the pages, each naming the current version of one stage."""

from abc import abstractmethod
from typing import TYPE_CHECKING

from bookreviver.domain.stages import PageStage, PageStageKey
from bookreviver.ports.persistence.base import KeyedRepository

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from bookreviver.domain.ids import (
        PageId,
        PageVersionId,
        RecipeId,
    )
    from bookreviver.domain.stage_summaries import StageTally, StepTally
    from bookreviver.domain.stages import Stage
    from bookreviver.domain.versions import ProcessorRef


class PageStageRepository(KeyedRepository[PageStage, PageStageKey]):
    """The current version of each stage of each page, addressed by the page and the stage.

    Deleting a page removes its records. A record keeps its row when its head version or its recipe is deleted, and
    loses only that reference. Storing a record raises ``NotFoundError`` when its page, its head version or its recipe
    is not stored, and ``find`` returns None for a stage that has not run on the page.
    """

    @abstractmethod
    def list_for_page(self, page_id: PageId) -> Sequence[PageStage]:
        """Return the records of one page in the order of the stages.

        :param page_id: Page owning the records.
        :type page_id: PageId
        :returns: Every record of the page.
        :rtype: Sequence[PageStage]
        """

    @abstractmethod
    def list_for_pages(self, page_ids: Collection[PageId]) -> Sequence[PageStage]:
        """Return the records of several pages in one read, by page and then in the order of the stages.

        :param page_ids: Pages whose records are read.
        :type page_ids: Collection[PageId]
        :returns: Every record of those pages.
        :rtype: Sequence[PageStage]
        """

    @abstractmethod
    def list_for_recipe(self, recipe_id: RecipeId) -> Sequence[PageStage]:
        """Return the records of the pages a recipe processed, by page identifier.

        :param recipe_id: Recipe whose pages are listed.
        :type recipe_id: RecipeId
        :returns: Every record that names the recipe.
        :rtype: Sequence[PageStage]
        """

    @abstractmethod
    def list_fresh(self) -> Sequence[PageStage]:
        """Return the records of the pages of the book whose current version is up to date.

        :returns: The records in the fresh state, by page identifier and then in the order of the stages.
        :rtype: Sequence[PageStage]
        """

    @abstractmethod
    def list_replaced(self, stage: Stage, processor: ProcessorRef) -> Sequence[PageStage]:
        """Return the records of a stage of the book whose current version a replaced version of a processor made.

        :param stage: The stage whose records are read.
        :type stage: Stage
        :param processor: The processor by key and installed version: a record is returned when its current version was
                          made by the same key in another version, whatever the state of the record.
        :type processor: ProcessorRef
        :returns: The records, by page identifier.
        :rtype: Sequence[PageStage]
        """

    @abstractmethod
    def list_for_stage(self, stage: Stage) -> Sequence[PageStage]:
        """Return the records of one stage over the pages of a book, by page identifier.

        :param stage: The stage.
        :type stage: Stage
        :returns: Every record of the stage in the book.
        :rtype: Sequence[PageStage]
        """

    @abstractmethod
    def step_tally(self) -> Sequence[StepTally]:
        """Count the pages of every stage of a book that a run stopped at each step, in one grouped query.

        Only pages with an image are counted, and a record that failed is left out, since its step belongs to a run that
        did not end. A page run through every step that is on has no step and is not counted.

        :returns: One tally for each step of a stage that a page stopped at, in no particular order.
        :rtype: Sequence[StepTally]
        """

    @abstractmethod
    def head_ids(self) -> Collection[PageVersionId]:
        """Return the identifiers of the versions that are the current version of a stage of a page of the book.

        :returns: The distinct head versions.
        :rtype: Collection[PageVersionId]
        """

    @abstractmethod
    def tally(self) -> Sequence[StageTally]:
        """Count the records of every stage of the book by state, in one grouped query.

        Only pages with an image are counted, so a placeholder never is. A page counts as marked for review when the
        current version of its record carries a review mark and the record is not failed. A page counts toward check
        when its record is stale or failed or the page is marked, once however many of these hold. A stage that no page
        has a record of has no tally.

        :returns: One tally for each stage that has a record, in no particular order.
        :rtype: Sequence[StageTally]
        """
