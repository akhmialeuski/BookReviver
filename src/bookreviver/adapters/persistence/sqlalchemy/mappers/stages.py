"""The mapper of the records of the stages of the pages."""

from typing import override

from bookreviver.adapters.persistence.sqlalchemy.mappers.base import RowMapper
from bookreviver.adapters.persistence.sqlalchemy.tables import PageStageRow
from bookreviver.domain.ids import (
    PageId,
    PageVersionId,
    RecipeId,
    StepId,
)
from bookreviver.domain.stages import PageStage


class PageStageMapper(RowMapper[PageStage, PageStageRow]):
    """Translation of the record of a stage of a page, whose columns are the fields of the entity."""

    @override
    def to_entity(self, row: PageStageRow) -> PageStage:
        """Build the record stored in ``row``.

        :param row: Page stage row loaded from the database.
        :type row: PageStageRow
        :returns: The record with its recipe, head version and state.
        :rtype: PageStage
        """
        return PageStage(
            page_id=PageId(row.page_id),
            stage=row.stage,
            recipe_id=None if row.recipe_id is None else RecipeId(row.recipe_id),
            head_version_id=None if row.head_version_id is None else PageVersionId(row.head_version_id),
            state=row.state,
            through_step_id=None if row.through_step_id is None else StepId(row.through_step_id),
            updated_at=row.updated_at,
        )

    @override
    def to_row(self, entity: PageStage) -> PageStageRow:
        """Build the row of ``entity``.

        :param entity: Record to store.
        :type entity: PageStage
        :returns: Transient page stage row.
        :rtype: PageStageRow
        """
        return PageStageRow(
            page_id=entity.page_id,
            stage=entity.stage,
            recipe_id=entity.recipe_id,
            head_version_id=entity.head_version_id,
            state=entity.state,
            through_step_id=entity.through_step_id,
            updated_at=entity.updated_at,
        )
