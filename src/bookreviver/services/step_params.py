"""The parameters the steps run with on one page, which a run, a preview and a split work out the same way.

The recipe gives a step its parameters, and a part of the pages may change some of the fields: the odd pages or the even
pages, the group of the page, or the page itself. ``StepValueLayers`` decides the order. ``PageLayers`` reads what it
needs for one page: the values of the parts of the book, once for every step of the page, and the place of the page
in the book, once and only when a part of the pages has a value for a step, since a run over a book that has none must
not pay for it. The reads are synchronous and go through the repositories the caller passes, so a run asks from the work
of ``CatalogueDatabase.read``; what has been read stays in the object, and the later calls, in the same transaction or
in another, read nothing again.
"""

from typing import TYPE_CHECKING

from attrs import frozen

from bookreviver.domain.sides import PageSide
from bookreviver.domain.step_values import StepValueLayers
from bookreviver.domain.versions import VersionInputs, VersionScale

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

    from bookreviver.core.plugins import Processor
    from bookreviver.domain.ids import PageVersionId, StepId
    from bookreviver.domain.metadata import MetadataMap
    from bookreviver.domain.page_steps import PageStepState
    from bookreviver.domain.pages import Page
    from bookreviver.domain.recipes import Step
    from bookreviver.domain.step_values import StepValues
    from bookreviver.ports.persistence import BookRepositories


@frozen(kw_only=True)
class StepRead:
    """What a step reads besides the page and its own state: the version before it, the scale, and what the book gives.

    :ivar input_id: Version the step reads, or None for a base version.
    :ivar scale: Whether the step runs on the full image or on the preview.
    :ivar book: The fields the book gives the step, laid over its parameters before the parts of the book, or None.
    """

    input_id: PageVersionId | None
    scale: VersionScale = VersionScale.FULL
    book: MetadataMap | None = None


class PageLayers:
    """Works out the parameters each step of a chain runs with on one page, over what is read for the page once.

    :ivar page: The page the steps run on.
    """

    def __init__(
        self,
        page: Page,
        *,
        parts: list[StepValues] | None = None,
        position: int | None = None,
        check: Callable[[Processor, MetadataMap], MetadataMap] | None = None,
    ) -> None:
        """Work for a page that is stored in the book.

        :param page: The page the steps run on.
        :type page: Page
        :param parts: The values of the parts of the book, when the caller read them for many pages, or None to read
                      them on the first call that needs them.
        :type parts: list[StepValues] | None
        :param position: The place of the page in the book from 1, when the caller read it, or None to count it.
        :type position: int | None
        :param check: What checks the parameters of a processor, or None for the processor itself, which a caller that
                      judges many pages replaces with one that remembers what it checked.
        :type check: Callable[[Processor, MetadataMap], MetadataMap] | None
        """
        self.page = page
        self._parts = parts
        self._position = position
        self._check = check

    def position(self, repos: BookRepositories) -> int:
        """Count the place of the page in the book, from 1, on the first call.

        :param repos: Repositories of the transaction the caller runs in, which are only read.
        :type repos: BookRepositories
        :returns: The place of the page, whose parity tells the odd pages from the even.
        :rtype: int
        """
        if self._position is None:
            self._position = repos.pages.count_before(self.page) + 1
        return self._position

    def lay(
        self, repos: BookRepositories, step_id: StepId, params: MetadataMap, own: MetadataMap | None
    ) -> dict[str, Any]:
        """Work out the parameters a step runs with on the page: the recipe, then the side, the group and the page.

        :param repos: Repositories of the transaction the caller runs in, which are only read.
        :type repos: BookRepositories
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
            self._parts = list(repos.step_values.list_all())
        parts = [values for values in self._parts if values.step_id == step_id]
        position = self.position(repos) if parts else 0
        return StepValueLayers(parts).lay_over(params, group_label=self.page.group_label, position=position, own=own)

    def inputs(
        self, repos: BookRepositories, step: Step, processor: Processor, state: PageStepState | None, read: StepRead
    ) -> VersionInputs:
        """Work out everything the version of a step on the page depends on, whose hash identifies the version.

        A run finds the version it made before by these inputs, and the freshness of a stage asks whether the current
        version of the stage is the one they give now, so the two work them out by this one method. The values of the
        page and of the parts of the book it is in are laid over the parameters of the step before they are checked, so
        two pages that end up with equal ones share a version. The side is part of what a step depends on, so a page
        moved to the other side of the book is made again. A leaf the program drew passes every step unchanged, with no
        parameters and no edit.

        :param repos: Repositories of the transaction the caller runs in, which are only read.
        :type repos: BookRepositories
        :param step: The step of the recipe.
        :type step: Step
        :param processor: The processor of the step.
        :type processor: Processor
        :param state: What the page keeps for the step, its own settings and its edit, or None.
        :type state: PageStepState | None
        :param read: The version the step reads, its scale and what the book gives it.
        :type read: StepRead
        :returns: The inputs of the version.
        :rtype: VersionInputs
        :raises InvalidParametersError: If the parameters do not fit the processor.
        """
        skipped = self.page.is_leaf
        params: MetadataMap = {}
        edit_hash = ''
        if not skipped:
            base = {**step.params, **read.book} if read.book else step.params
            laid = self.lay(repos, step.step_id, base, None if state is None else state.params)
            params = processor.validate_params(laid) if self._check is None else self._check(processor, laid)
            edit_hash = '' if state is None or state.edit is None else state.edit.edit_hash
        by_side = processor.spec.by_page_side and not skipped
        return VersionInputs(
            page_id=self.page.id,
            processor=processor.spec.ref,
            params=params,
            input_id=read.input_id,
            edit_hash=edit_hash,
            scale=read.scale,
            side=PageSide.of_position(self.position(repos)) if by_side else None,
            skipped=skipped,
        )
