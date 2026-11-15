"""The repository of the records of the stages of the pages, with the tallies of the stages of a book."""

from typing import TYPE_CHECKING, override

from sqlalchemy import and_, case, func, or_, select

from bookreviver.adapters.persistence.sqlalchemy.mappers import PageStageMapper
from bookreviver.adapters.persistence.sqlalchemy.repositories.base import SqlAlchemyKeyedRepository
from bookreviver.adapters.persistence.sqlalchemy.rows import PageStageRows
from bookreviver.adapters.persistence.sqlalchemy.tables import (
    PageRow,
    PageStageRow,
    PageVersionRow,
)
from bookreviver.domain.ids import PageVersionId, StepId
from bookreviver.domain.pages import PageOrigin
from bookreviver.domain.stage_summaries import StageTally, StepTally
from bookreviver.domain.stages import PageStage, PageStageKey, Stage, StageState
from bookreviver.ports.persistence import PageStageRepository

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from sqlalchemy.orm import Session

    from bookreviver.domain.ids import (
        PageId,
        RecipeId,
    )
    from bookreviver.domain.versions import ProcessorRef


class SqlAlchemyPageStageRepository(
    SqlAlchemyKeyedRepository[PageStage, PageStageKey, PageStageRow], PageStageRepository
):
    """The current version of each stage of each page, addressed by the page and the stage."""

    def __init__(self, session: Session) -> None:
        """Create the repository over the ``page_stages`` table.

        :param session: Session of the transaction.
        :type session: Session
        """
        super().__init__(rows=PageStageRows(session=session), mapper=PageStageMapper())

    @override
    def get(self, entity_id: PageStageKey) -> PageStage:
        """Return the record of a stage of a page.

        :param entity_id: Page and stage.
        :type entity_id: PageStageKey
        :returns: The stored record.
        :rtype: PageStage
        :raises NotFoundError: If the stage has no record on the page.
        """
        return self._mapper.to_entity(self._rows.get((entity_id.page_id, entity_id.stage)))

    @override
    def delete(self, entity_id: PageStageKey) -> None:
        """Remove the record of a stage of a page.

        :param entity_id: Page and stage.
        :type entity_id: PageStageKey
        :raises NotFoundError: If the stage has no record on the page.
        """
        self._rows.delete((entity_id.page_id, entity_id.stage))

    @override
    def list_for_page(self, page_id: PageId) -> Sequence[PageStage]:
        """Return the records of one page in the order of the stages.

        :param page_id: Page owning the records.
        :type page_id: PageId
        :returns: Every record of the page.
        :rtype: Sequence[PageStage]
        """
        order = list(Stage)
        records = [self._mapper.to_entity(row) for row in self._rows.get_many(page_id=page_id)]
        return sorted(records, key=lambda record: order.index(record.stage))

    @override
    def list_for_pages(self, page_ids: Collection[PageId]) -> Sequence[PageStage]:
        """Return the records of several pages in one ``IN`` query, by page and then in the order of the stages.

        :param page_ids: Pages whose records are read.
        :type page_ids: Collection[PageId]
        :returns: Every record of those pages.
        :rtype: Sequence[PageStage]
        """
        order = list(Stage)
        rows = self._rows.get_many_in(PageStageRow.page_id, page_ids)
        records = [self._mapper.to_entity(row) for row in rows]
        return sorted(records, key=lambda record: (str(record.page_id), order.index(record.stage)))

    @override
    def list_for_recipe(self, recipe_id: RecipeId) -> Sequence[PageStage]:
        """Return the records that name the recipe, by page identifier.

        :param recipe_id: Recipe whose pages are listed.
        :type recipe_id: RecipeId
        :returns: Every record that names the recipe.
        :rtype: Sequence[PageStage]
        """
        rows = self._rows.get_many(order_by=PageStageRow.page_id.asc(), recipe_id=recipe_id)
        return [self._mapper.to_entity(row) for row in rows]

    @override
    def list_fresh(self) -> Sequence[PageStage]:
        """Return the records in the fresh state, by page and then in the order of the stages.

        :returns: The fresh records of the book.
        :rtype: Sequence[PageStage]
        """
        order = list(Stage)
        records = [self._mapper.to_entity(row) for row in self._rows.get_many(state=StageState.FRESH)]
        return sorted(records, key=lambda record: (str(record.page_id), order.index(record.stage)))

    @override
    def list_replaced(self, stage: Stage, processor: ProcessorRef) -> Sequence[PageStage]:
        """Return the records of a stage whose current version a replaced version of a processor made, in one query.

        :param stage: The stage whose records are read.
        :type stage: Stage
        :param processor: The processor by key and installed version.
        :type processor: ProcessorRef
        :returns: The records whose current version was made by the key in another version, by page identifier.
        :rtype: Sequence[PageStage]
        """
        statement = (
            select(PageStageRow)
            .join(PageVersionRow, PageStageRow.head_version_id == PageVersionRow.id)
            .where(
                PageStageRow.stage == stage,
                PageVersionRow.processor_key == processor.key,
                PageVersionRow.processor_version != processor.version,
            )
            .order_by(PageStageRow.page_id)
        )
        return [self._mapper.to_entity(row) for row in self._rows.session.scalars(statement).all()]

    @override
    def list_for_stage(self, stage: Stage) -> Sequence[PageStage]:
        """Return the records of one stage over the pages of a book, by page identifier.

        :param stage: The stage.
        :type stage: Stage
        :returns: Every record of the stage in the book.
        :rtype: Sequence[PageStage]
        """
        statement = (
            select(PageStageRow)
            .join(PageRow, PageStageRow.page_id == PageRow.id)
            .where(PageStageRow.stage == stage)
            .order_by(PageStageRow.page_id)
        )
        return [self._mapper.to_entity(row) for row in self._rows.session.scalars(statement).all()]

    @override
    def head_ids(self) -> Collection[PageVersionId]:
        """Return the distinct head versions of the stage records of the book's pages.

        :returns: The identifiers of the current versions.
        :rtype: Collection[PageVersionId]
        """
        statement = (
            select(PageStageRow.head_version_id)
            .join(PageRow, PageStageRow.page_id == PageRow.id)
            .where(PageStageRow.head_version_id.is_not(None))
            .distinct()
        )
        return {PageVersionId(version_id) for version_id in self._rows.session.scalars(statement).all() if version_id}

    @override
    def tally(self) -> Sequence[StageTally]:
        """Count the records of every stage of the book by state with one grouped statement.

        The counts are conditional sums, which every database computes alike, and the head version is joined outside
        so a record without one still counts. The check count is one more sum over the same rows, so a page that is
        both stale and marked is counted once. Placeholders have no image, so their records are not counted.

        :returns: One tally for each stage that has a record.
        :rtype: Sequence[StageTally]
        """
        marked = and_(PageStageRow.state != StageState.FAILED, PageVersionRow.review.is_not(None))
        fresh_sum, stale_sum, failed_sum, review_sum, check_sum, partial_sum = (
            func.coalesce(func.sum(case((condition, 1), else_=0)), 0)
            for condition in (
                PageStageRow.state == StageState.FRESH,
                PageStageRow.state == StageState.STALE,
                PageStageRow.state == StageState.FAILED,
                marked,
                or_(PageStageRow.state != StageState.FRESH, marked),
                and_(PageStageRow.state != StageState.FAILED, PageStageRow.through_step_id.is_not(None)),
            )
        )
        statement = (
            select(
                PageStageRow.stage,
                fresh_sum,
                stale_sum,
                failed_sum,
                review_sum,
                check_sum,
                partial_sum,
            )
            .select_from(PageStageRow)
            .join(PageRow, PageStageRow.page_id == PageRow.id)
            .outerjoin(PageVersionRow, PageStageRow.head_version_id == PageVersionRow.id)
            .where(PageRow.origin != PageOrigin.PLACEHOLDER)
            .group_by(PageStageRow.stage)
        )
        return [
            StageTally(
                stage=stage,
                fresh=fresh,
                stale=stale,
                failed=failed,
                review=review,
                check=check,
                partial=partial,
            )
            for stage, fresh, stale, failed, review, check, partial in self._rows.session.execute(statement)
        ]

    @override
    def step_tally(self) -> Sequence[StepTally]:
        """Count the pages a run stopped at each step, for every stage of the book, with one grouped statement.

        :returns: One tally for each step of a stage that a page stopped at.
        :rtype: Sequence[StepTally]
        """
        statement = (
            select(PageStageRow.stage, PageStageRow.through_step_id, func.count())
            .join(PageRow, PageStageRow.page_id == PageRow.id)
            .where(
                PageRow.origin != PageOrigin.PLACEHOLDER,
                PageStageRow.state != StageState.FAILED,
                PageStageRow.through_step_id.is_not(None),
            )
            .group_by(PageStageRow.stage, PageStageRow.through_step_id)
        )
        return [
            StepTally(stage=stage, through_step_id=StepId(through_step_id), pages=pages)
            for stage, through_step_id, pages in self._rows.session.execute(statement)
            if through_step_id is not None
        ]
