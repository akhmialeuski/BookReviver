"""The parameters the steps run with on one page, which a run, a preview and a split work out the same way.

The recipe gives a step its parameters, and a part of the pages may change some of the fields: the odd pages or the even
pages, the group of the page, or the page itself. ``StepValueLayers`` decides the order. ``PageLayers`` reads what it
needs for one page: the values of the parts of the project, once for every step of the page, and the place of the page
in the book, once and only when a part of the pages has a value for a step, since a run over a book that has none must
not pay for it.
"""

from typing import TYPE_CHECKING

from bookreviver.domain.step_values import StepValueLayers

if TYPE_CHECKING:
    from typing import Any

    from bookreviver.domain.entities import Page
    from bookreviver.domain.ids import StepId
    from bookreviver.domain.step_values import StepValues
    from bookreviver.domain.values import MetadataMap
    from bookreviver.ports.persistence import UnitOfWork


class PageLayers:
    """Works out the parameters each step of a chain runs with on one page, over what is read for the page once.

    :ivar page: The page the steps run on.
    """

    def __init__(self, uow: UnitOfWork, page: Page) -> None:
        """Read through the unit of work of the run, for a page that is stored in the book.

        :param uow: Unit of work the values of the steps and the place of the page are read through.
        :type uow: UnitOfWork
        :param page: The page the steps run on.
        :type page: Page
        """
        self._uow = uow
        self.page = page
        self._parts: list[StepValues] | None = None
        self._position: int | None = None

    async def position(self) -> int:
        """Count the place of the page in the book, from 1, on the first call.

        :returns: The place of the page, whose parity tells the odd pages from the even.
        :rtype: int
        """
        if self._position is None:
            self._position = await self._uow.pages.count_before(self.page) + 1
        return self._position

    async def lay(self, step_id: StepId, params: MetadataMap, own: MetadataMap | None) -> dict[str, Any]:
        """Work out the parameters a step runs with on the page: the recipe, then the side, the group and the page.

        :param step_id: The step of a recipe.
        :type step_id: StepId
        :param params: Parameters of the step as the recipe, or the form of a preview, gives them.
        :type params: MetadataMap
        :param own: The fields the page changes for itself, or None for a page that changes none.
        :type own: MetadataMap | None
        :returns: The parameters with each field taken from the strongest part that has a value for it.
        :rtype: dict[str, Any]
        """
        if self._parts is None:
            self._parts = list(await self._uow.step_values.list_for_project(self.page.project_id))
        parts = [values for values in self._parts if values.step_id == step_id]
        position = await self.position() if parts else 0
        return StepValueLayers(parts).lay_over(params, group_label=self.page.group_label, position=position, own=own)
