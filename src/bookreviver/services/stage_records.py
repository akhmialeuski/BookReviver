"""The records of the current version of each stage of a page, and the rule that a changed stage makes later ones stale.

A page version never knows whether it is current: the ``PageStage`` record of its stage does. When the current version
of a stage changes, the later stages of the same page were computed from the old one, so their records are marked
stale and their versions stay, which lets the interface show the old result with a mark until the user computes it
again. A record that already failed stays failed, since a stale mark would hide the failure.

Every method that writes or reads records takes the ``BookRepositories`` of the transaction it runs in, so it is called
from the work of ``Book.change`` or ``Book.read`` and opens no transaction of its own. It is
synchronous, like the work that calls it, and announces nothing: the browser is told that the stages
changed once the transaction committed.

A value for the odd pages or the even pages is taken by place, so a page that a change of the places of the pages turns
over to the other side runs with other parameters, and its stages are marked stale by ``watching_sides``.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Self

from attrs import evolve

from bookreviver.domain.entities import PageStage
from bookreviver.domain.enums import Stage, StageState, ValueScope
from bookreviver.domain.step_values import SIDE_SCOPES, pages_changing_side
from bookreviver.domain.values import PageStageKey
from bookreviver.services.books import book_pages

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable
    from types import TracebackType

    from bookreviver.domain.ids import PageId, PageVersionId, RecipeId
    from bookreviver.ports.persistence import BookRepositories


class StageRecords:
    """Writes the stage records of pages inside the transaction of the caller."""

    def set_head(
        self,
        repos: BookRepositories,
        key: PageStageKey,
        *,
        head_version_id: PageVersionId,
        recipe_id: RecipeId | None,
        through_step: int | None = None,
    ) -> PageStage:
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
        :returns: The record of the stage in its new state.
        :rtype: PageStage
        """
        previous = repos.page_stages.find(key)
        record = PageStage(
            page_id=key.page_id,
            stage=key.stage,
            recipe_id=recipe_id,
            head_version_id=head_version_id,
            state=StageState.FRESH,
            through_step=through_step,
            updated_at=datetime.now(UTC),
        )
        repos.page_stages.save(record)
        if previous is None or previous.head_version_id != head_version_id:
            self._mark_later_stale(repos, key.page_id, key.stage)
        return record

    def clear(self, repos: BookRepositories, key: PageStageKey) -> None:
        """Remove the record of a stage of a page that has no current version of its own any longer.

        The later stages of the page were computed from the version the record named, so they are marked stale, and the
        record of a stage the page never had is nothing to remove.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param key: Page and stage whose record is removed.
        :type key: PageStageKey
        """
        if repos.page_stages.find(key) is None:
            return
        repos.page_stages.delete(key)
        self._mark_later_stale(repos, key.page_id, key.stage)

    def mark_stale(self, repos: BookRepositories, page_id: PageId, stage: Stage) -> None:
        """Mark one stage of a page stale, because its recipe or a manual edit it reads changed.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param page_id: Page whose stage is marked.
        :type page_id: PageId
        :param stage: The stage.
        :type stage: Stage
        """
        record = repos.page_stages.find(PageStageKey(page_id, stage))
        self._stale(repos, [] if record is None else [record])

    def mark_pages_stale(
        self, repos: BookRepositories, page_ids: Collection[PageId], stages: Collection[Stage]
    ) -> None:
        """Mark some stages of some pages stale, which is one read for all of them.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param page_ids: Pages whose stages are marked.
        :type page_ids: Collection[PageId]
        :param stages: The stages to mark.
        :type stages: Collection[Stage]
        """
        if not page_ids or not stages:
            return
        self._stale(repos, (record for record in repos.page_stages.list_for_pages(page_ids) if record.stage in stages))

    def mark_group_stale(self, repos: BookRepositories, page_id: PageId, labels: Collection[str]) -> None:
        """Mark the stages of a page stale in which a group it joined or left has values for a step.

        A page takes the values of its group over those of its side, so a page that changes its group runs with other
        parameters wherever one of the two groups has a value, and in no other stage.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param page_id: Page whose group changed.
        :type page_id: PageId
        :param labels: The group the page was in and the one it is in now, either of which may be empty for none.
        :type labels: Collection[str]
        """
        stages = {
            values.stage
            for values in repos.step_values.list_all()
            if values.scope is ValueScope.GROUP and values.group_label in labels
        }
        self.mark_pages_stale(repos, [page_id], stages)

    def mark_sides_stale(self, repos: BookRepositories, page_ids: Collection[PageId]) -> None:
        """Mark the stages of pages stale in which the odd pages or the even pages have values for a step.

        A page takes the values of the odd or the even pages by its place in the book, so a page whose place changed
        by an odd number runs with other parameters wherever one of the two sides has a value, and in no other stage.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param page_ids: Pages that stand on the other side of the book than they did.
        :type page_ids: Collection[PageId]
        """
        self.mark_pages_stale(repos, page_ids, self.side_stages(repos))

    def watching_sides(self, repos: BookRepositories) -> SideWatch:
        """Watch the pages of a book for the ones that a block of code turns over to the other side of the book.

        :param repos: Repositories of the transaction the block runs in, which moves, adds or deletes pages.
        :type repos: BookRepositories
        :returns: The watch, which is entered with ``with`` around the block, inside the work of the caller.
        :rtype: SideWatch
        """
        return SideWatch(records=self, repos=repos)

    @staticmethod
    def side_stages(repos: BookRepositories) -> set[Stage]:
        """Find the stages in which the odd pages or the even pages of a book have values for a step.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :returns: The stages, none when no side has a value.
        :rtype: set[Stage]
        """
        return {values.stage for values in repos.step_values.list_all() if values.scope in SIDE_SCOPES}

    def mark_content_stale(self, repos: BookRepositories, page_id: PageId) -> None:
        """Mark every stage of a page after the page order stale, because what the page shows changed.

        A page is processed by the recipe of its kind, so a page whose kind changed is processed by other steps than the
        ones that made its current versions.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param page_id: Page whose kind changed.
        :type page_id: PageId
        """
        self._stale(
            repos,
            (
                record
                for record in repos.page_stages.list_for_page(page_id)
                if record.stage.position > Stage.PAGE_ORDER.position
            ),
        )

    def mark_recipe_stale(self, repos: BookRepositories, recipe_id: RecipeId) -> None:
        """Mark the stage of every page a recipe processed stale, because the recipe changed.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param recipe_id: Recipe that changed.
        :type recipe_id: RecipeId
        """
        self._stale(repos, repos.page_stages.list_for_recipe(recipe_id))

    def mark_failed(
        self, repos: BookRepositories, page_id: PageId, stage: Stage, *, recipe_id: RecipeId | None
    ) -> None:
        """Record that a stage failed on a page, keeping the version that was current before.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param page_id: Page whose stage failed.
        :type page_id: PageId
        :param stage: The stage.
        :type stage: Stage
        :param recipe_id: Recipe that failed.
        :type recipe_id: RecipeId | None
        """
        previous = repos.page_stages.find(PageStageKey(page_id, stage))
        record = PageStage(
            page_id=page_id,
            stage=stage,
            recipe_id=recipe_id,
            head_version_id=None if previous is None else previous.head_version_id,
            state=StageState.FAILED,
            through_step=None if previous is None else previous.through_step,
            updated_at=datetime.now(UTC),
        )
        repos.page_stages.save(record)

    def _mark_later_stale(self, repos: BookRepositories, page_id: PageId, stage: Stage) -> None:
        """Mark every up-to-date stage after ``stage`` stale.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param page_id: Page whose later stages are marked.
        :type page_id: PageId
        :param stage: The stage that changed.
        :type stage: Stage
        """
        self._stale(
            repos,
            (later for later in repos.page_stages.list_for_page(page_id) if later.stage.position > stage.position),
        )

    def _stale(self, repos: BookRepositories, records: Iterable[PageStage]) -> None:
        """Write the up-to-date ones of some records as stale in one statement, and leave the others as they are.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param records: Records read in the transaction of the caller.
        :type records: Iterable[PageStage]
        """
        stale = [
            evolve(record, state=StageState.STALE, updated_at=datetime.now(UTC))
            for record in records
            if record.state is StageState.FRESH
        ]
        repos.page_stages.save_many(stale)


class SideWatch:
    """Marks stale the pages that the places a block changes turn over to the other side of the book.

    The order of the book is read before the block and after it, so inserting, deleting and moving pages are all
    covered by the one comparison, and nothing is read when no side has values for a step. The watch is a synchronous
    context manager entered inside the work of ``Book.change`` and marks the records there. A block that raises marks
    nothing.
    """

    def __init__(self, *, records: StageRecords, repos: BookRepositories) -> None:
        """Watch the pages of a book through the repositories of the caller's transaction.

        :param records: Writer of the stage records.
        :type records: StageRecords
        :param repos: Repositories of the transaction the watched block runs in.
        :type repos: BookRepositories
        """
        self._records = records
        self._repos = repos
        self._before: list[PageId] | None = None

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
            self._records.mark_sides_stale(self._repos, pages_changing_side(self._before, self._order()))

    def _order(self) -> list[PageId]:
        """Read the pages of the book in book order, the placeholders and the pages kept out of the book included.

        :returns: The identifiers of the pages, by place.
        :rtype: list[PageId]
        """
        return [page.id for page in book_pages(self._repos.pages)]
