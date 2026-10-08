"""The parameters a step runs with on one page, which a run, a preview and a split work out the same way.

The recipe gives a step its parameters, and a part of the pages may change some of the fields: the odd pages or the even
pages, the group of the page, or the page itself. ``StepValueLayers`` decides the order. This module reads what it needs
for one page from the stored values, so the place of the page in the book is counted only when a part of the pages has
a value for the step, since a run over a book that has none must not pay for it.
"""

from typing import TYPE_CHECKING

from bookreviver.domain.step_values import StepValueLayers

if TYPE_CHECKING:
    from typing import Any

    from bookreviver.domain.entities import Page
    from bookreviver.domain.ids import StepId
    from bookreviver.domain.values import MetadataMap
    from bookreviver.ports.persistence import UnitOfWork


async def lay_step_values(
    uow: UnitOfWork, page: Page, step_id: StepId, params: MetadataMap, own: MetadataMap | None
) -> dict[str, Any]:
    """Work out the parameters a step runs with on a page: the recipe, then the side, the group and the page.

    :param uow: Unit of work the values of the step are read through.
    :type uow: UnitOfWork
    :param page: The page the step runs on, which is a stored page of the book.
    :type page: Page
    :param step_id: The step of a recipe.
    :type step_id: StepId
    :param params: Parameters of the step as the recipe, or the form of a preview, gives them.
    :type params: MetadataMap
    :param own: The fields the page changes for itself, or None for a page that changes none.
    :type own: MetadataMap | None
    :returns: The parameters with each field taken from the strongest part that has a value for it.
    :rtype: dict[str, Any]
    """
    parts = await uow.step_values.list_for_step(step_id)
    position = await uow.pages.count_before(page) + 1 if parts else 0
    return StepValueLayers(parts).lay_over(params, group_label=page.group_label, position=position, own=own)
