"""The records of the current version of each stage of a page, and the rule that a changed stage makes later ones stale.

A page version never knows whether it is current: the ``PageStage`` record of its stage does. When the current version
of a stage changes, the later stages of the same page were computed from the old one, so their records are marked
stale and their versions stay, which lets the interface show the old result with a mark until the user computes it
again. A record that already failed stays failed, since a stale mark would hide the failure.

Every method that writes or reads records takes the ``BookRepositories`` of the transaction it runs in, so it is called
from the work of ``UnitOfWork.change_book`` or ``UnitOfWork.read_book`` and opens no transaction of its own. It is
synchronous, like the work that calls it. Only ``announce`` is asynchronous: the caller awaits it after the transaction
has ended, so a browser reads the stages it shows again.

A value for the odd pages or the even pages is taken by place, so a page that a change of the places of the pages turns
over to the other side runs with other parameters, and its stages are marked stale by ``watching_sides``.
"""

from typing import TYPE_CHECKING, Self

from attrs import evolve

from bookreviver.domain.entities import PageStage
from bookreviver.domain.enums import Stage, StageState, ValueScope
from bookreviver.domain.events import PageStageChanged
from bookreviver.domain.step_values import SIDE_SCOPES, pages_changing_side
from bookreviver.domain.values import PageStageKey
from bookreviver.services.projects import book_pages

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Sequence
    from types import TracebackType

    from bookreviver.domain.ids import PageId, PageVersionId, ProjectId, RecipeId
    from bookreviver.ports.persistence import BookRepositories
    from bookreviver.ports.runtime import Clock, EventPublisher


class StageRecords:
    """Writes the stage records of pages inside the transaction of the caller, and announces the ones that changed."""

    def __init__(self, *, publisher: EventPublisher, clock: Clock) -> None:
        """Stamp the records with a clock and announce them through a publisher.

        :param publisher: Publisher of the events the browser follows.
        :type publisher: EventPublisher
        :param clock: Clock stamping the records.
        :type clock: Clock
        """
        self._publisher = publisher
        self._clock = clock

    def set_head(
        self,
        repos: BookRepositories,
        key: PageStageKey,
        *,
        head_version_id: PageVersionId,
        recipe_id: RecipeId | None,
        through_step: int | None = None,
    ) -> Sequence[PageStage]:
        """Make a version the current one of a stage, and mark the later stages of the page stale if it changed.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param key: Page and stage whose record changes.
        :type key: PageStageKey
        :param head_version_id: Version that becomes the current one.
        :type head_version_id: PageVersionId
        :param recipe_id: Recipe the page was processed by, or None for a version no recipe made.
        :type recipe_id: RecipeId | None
        :param through_step: Index in the recipe of the step the version is the result of when the run stopped before
                             the last step that is on, or None when it went through all of them.
        :type through_step: int | None
        :returns: The records that changed, the stage first, then the later stages that became stale.
        :rtype: Sequence[PageStage]
        """
        previous = repos.page_stages.find(key)
        record = PageStage(
            page_id=key.page_id,
            stage=key.stage,
            recipe_id=recipe_id,
            head_version_id=head_version_id,
            state=StageState.FRESH,
            through_step=through_step,
            updated_at=self._clock.now(),
        )
        repos.page_stages.save(record)
        changed = [record]
        if previous is None or previous.head_version_id != head_version_id:
            changed.extend(self._mark_later_stale(repos, key.page_id, key.stage))
        return changed

    def clear(self, repos: BookRepositories, key: PageStageKey) -> list[PageStage]:
        """Remove the record of a stage of a page that has no current version of its own any longer.

        The later stages of the page were computed from the version the record named, so they are marked stale, and the
        record of a stage the page never had is nothing to remove.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param key: Page and stage whose record is removed.
        :type key: PageStageKey
        :returns: The later stages that became stale.
        :rtype: list[PageStage]
        """
        if repos.page_stages.find(key) is None:
            return []
        repos.page_stages.delete(key)
        return self._mark_later_stale(repos, key.page_id, key.stage)

    def mark_stale(self, repos: BookRepositories, page_id: PageId, stage: Stage) -> list[PageStage]:
        """Mark one stage of a page stale, because its recipe or a manual edit it reads changed.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param page_id: Page whose stage is marked.
        :type page_id: PageId
        :param stage: The stage.
        :type stage: Stage
        :returns: The record if it changed, and none for a stage that has not run or is stale or failed already.
        :rtype: list[PageStage]
        """
        record = repos.page_stages.find(PageStageKey(page_id, stage))
        return self._stale(repos, [] if record is None else [record])

    def mark_pages_stale(
        self, repos: BookRepositories, page_ids: Collection[PageId], stages: Collection[Stage]
    ) -> list[PageStage]:
        """Mark some stages of some pages stale, which is one read for all of them.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param page_ids: Pages whose stages are marked.
        :type page_ids: Collection[PageId]
        :param stages: The stages to mark.
        :type stages: Collection[Stage]
        :returns: The records that became stale, none for a stage that has not run or is stale or failed already.
        :rtype: list[PageStage]
        """
        if not page_ids or not stages:
            return []
        return self._stale(
            repos, (record for record in repos.page_stages.list_for_pages(page_ids) if record.stage in stages)
        )

    def mark_group_stale(self, repos: BookRepositories, page_id: PageId, labels: Collection[str]) -> list[PageStage]:
        """Mark the stages of a page stale in which a group it joined or left has values for a step.

        A page takes the values of its group over those of its side, so a page that changes its group runs with other
        parameters wherever one of the two groups has a value, and in no other stage.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param page_id: Page whose group changed.
        :type page_id: PageId
        :param labels: The group the page was in and the one it is in now, either of which may be empty for none.
        :type labels: Collection[str]
        :returns: The records that became stale, none for a page that has not been through such a stage.
        :rtype: list[PageStage]
        """
        stages = {
            values.stage
            for values in repos.step_values.list_all()
            if values.scope is ValueScope.GROUP and values.group_label in labels
        }
        return self.mark_pages_stale(repos, [page_id], stages)

    def mark_sides_stale(self, repos: BookRepositories, page_ids: Collection[PageId]) -> list[PageStage]:
        """Mark the stages of pages stale in which the odd pages or the even pages have values for a step.

        A page takes the values of the odd or the even pages by its place in the book, so a page whose place changed
        by an odd number runs with other parameters wherever one of the two sides has a value, and in no other stage.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param page_ids: Pages that stand on the other side of the book than they did.
        :type page_ids: Collection[PageId]
        :returns: The records that became stale, none for a page that has not been through such a stage.
        :rtype: list[PageStage]
        """
        return self.mark_pages_stale(repos, page_ids, self.side_stages(repos))

    def watching_sides(self, repos: BookRepositories, project_id: ProjectId) -> SideWatch:
        """Watch the pages of a project for the ones that a block of code turns over to the other side of the book.

        :param repos: Repositories of the transaction the block runs in.
        :type repos: BookRepositories
        :param project_id: Project whose pages the block moves, adds or deletes.
        :type project_id: ProjectId
        :returns: The watch, which is entered with ``with`` around the block, inside the work of the caller.
        :rtype: SideWatch
        """
        return SideWatch(records=self, repos=repos, project_id=project_id)

    @staticmethod
    def side_stages(repos: BookRepositories) -> set[Stage]:
        """Find the stages in which the odd pages or the even pages of a project have values for a step.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :returns: The stages, none when no side has a value.
        :rtype: set[Stage]
        """
        return {values.stage for values in repos.step_values.list_all() if values.scope in SIDE_SCOPES}

    def mark_content_stale(self, repos: BookRepositories, page_id: PageId) -> list[PageStage]:
        """Mark every stage of a page after the page order stale, because what the page shows changed.

        A page is processed by the recipe of its kind, so a page whose kind changed is processed by other steps than the
        ones that made its current versions.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param page_id: Page whose kind changed.
        :type page_id: PageId
        :returns: The records that became stale, none for a page that has not been through such a stage or whose stages
                  are stale or failed already.
        :rtype: list[PageStage]
        """
        return self._stale(
            repos,
            (
                record
                for record in repos.page_stages.list_for_page(page_id)
                if record.stage.position > Stage.PAGE_ORDER.position
            ),
        )

    def mark_recipe_stale(self, repos: BookRepositories, recipe_id: RecipeId) -> list[PageStage]:
        """Mark the stage of every page a recipe processed stale, because the recipe changed.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param recipe_id: Recipe that changed.
        :type recipe_id: RecipeId
        :returns: The records that became stale.
        :rtype: list[PageStage]
        """
        return self._stale(repos, repos.page_stages.list_for_recipe(recipe_id))

    def mark_failed(
        self, repos: BookRepositories, page_id: PageId, stage: Stage, *, recipe_id: RecipeId | None
    ) -> PageStage:
        """Record that a stage failed on a page, keeping the version that was current before.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param page_id: Page whose stage failed.
        :type page_id: PageId
        :param stage: The stage.
        :type stage: Stage
        :param recipe_id: Recipe that failed.
        :type recipe_id: RecipeId | None
        :returns: The record in its new state.
        :rtype: PageStage
        """
        previous = repos.page_stages.find(PageStageKey(page_id, stage))
        record = PageStage(
            page_id=page_id,
            stage=stage,
            recipe_id=recipe_id,
            head_version_id=None if previous is None else previous.head_version_id,
            state=StageState.FAILED,
            through_step=None if previous is None else previous.through_step,
            updated_at=self._clock.now(),
        )
        repos.page_stages.save(record)
        return record

    async def announce(self, project_id: ProjectId, records: Sequence[PageStage]) -> None:
        """Publish one event for each record that changed, after the transaction that wrote them has ended.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param records: The records that changed.
        :type records: Sequence[PageStage]
        """
        for record in records:
            await self._publisher.publish(PageStageChanged(project_id=project_id, stage=record))

    def _mark_later_stale(self, repos: BookRepositories, page_id: PageId, stage: Stage) -> list[PageStage]:
        """Mark every up-to-date stage after ``stage`` stale.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param page_id: Page whose later stages are marked.
        :type page_id: PageId
        :param stage: The stage that changed.
        :type stage: Stage
        :returns: The records that became stale.
        :rtype: list[PageStage]
        """
        return self._stale(
            repos,
            (later for later in repos.page_stages.list_for_page(page_id) if later.stage.position > stage.position),
        )

    def _stale(self, repos: BookRepositories, records: Iterable[PageStage]) -> list[PageStage]:
        """Write the up-to-date ones of some records as stale in one statement, and leave the others as they are.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param records: Records read in the transaction of the caller.
        :type records: Iterable[PageStage]
        :returns: The records that became stale.
        :rtype: list[PageStage]
        """
        stale = [
            evolve(record, state=StageState.STALE, updated_at=self._clock.now())
            for record in records
            if record.state is StageState.FRESH
        ]
        repos.page_stages.save_many(stale)
        return stale


class SideWatch:
    """Marks stale the pages that the places a block changes turn over to the other side of the book.

    The order of the book is read before the block and after it, so inserting, deleting and moving pages are all
    covered by the one comparison, and nothing is read when no side has values for a step. The watch is a synchronous
    context manager entered inside the work of ``change_book`` and marks the records there, and the caller announces
    them after the work. A block that raises marks nothing.

    :ivar turned: The records that became stale, which are none until a block ends without an error.
    """

    def __init__(self, *, records: StageRecords, repos: BookRepositories, project_id: ProjectId) -> None:
        """Watch the pages of a project through the repositories of the caller's transaction.

        :param records: Writer of the stage records.
        :type records: StageRecords
        :param repos: Repositories of the transaction the watched block runs in.
        :type repos: BookRepositories
        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        """
        self._records = records
        self._repos = repos
        self._project_id = project_id
        self._before: list[PageId] | None = None
        self.turned: list[PageStage] = []

    def __enter__(self) -> Self:
        """Remember the order of the book, when a side has values for a step.

        :returns: The watch.
        :rtype: Self
        """
        if self._records.side_stages(self._repos):
            self._before = self._order()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Mark the pages that stand on the other side now, unless the block raised.

        :param exc_type: Type of the exception the block raised, or None.
        :type exc_type: type[BaseException] | None
        :param exc: The exception the block raised, or None.
        :type exc: BaseException | None
        :param traceback: Traceback of the exception, or None.
        :type traceback: TracebackType | None
        """
        if exc_type is None and self._before is not None:
            turned = pages_changing_side(self._before, self._order())
            self.turned = self._records.mark_sides_stale(self._repos, turned)

    def _order(self) -> list[PageId]:
        """Read the pages of the project in book order, the placeholders and the pages kept out of the book included.

        :returns: The identifiers of the pages, by place.
        :rtype: list[PageId]
        """
        return [page.id for page in book_pages(self._repos.pages)]
