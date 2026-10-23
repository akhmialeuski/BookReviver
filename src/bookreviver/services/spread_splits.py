"""Splitting a scan into the two pages of a spread, and undoing the split.

A processor of the scope ``split`` reads the scan of a page and makes one output for each part of it, the left half and
then the right half. The page that runs the step keeps its place and becomes the left half, slot 1, and the right half
is a page of its own, slot 2, inserted into the book right after it. Each half has a base version of the stage, which
holds the copy of its part of the scan, so the page stands on its own as any other. The identifier of a base version
comes from ``VersionInputs`` like that of every version, with the manual cut line of the step as the edit, so a changed
line gives new versions of both halves and the old line finds the old ones again.

Nothing of the book changes until the halves exist. The step runs first and its files are written, with no transaction
held, and then the work of one ``change_book`` reads the page and its scan again and stores the new page, the change of
the left page, the versions of both halves and their heads together, so a split that fails leaves the book as it was,
with a failed version on the page that ran it. A page that was deleted meanwhile, or whose scan got another right half,
is not split: the files are discarded and the page that is still there is left with a failed version that says the book
changed. The right half is made once. Splitting again finds its page by the scan and the slot and gives it new
versions, and a stage run that goes by the pages of a book leaves the right halves to the run of their left halves.

A step of the scope ``split`` may decide for each scan, and keep it whole: it then makes one output, the page that runs
the step becomes the whole scan, and a split made earlier is undone as described below, with the same confirmation.

Undoing a split is running a step of the scope ``page`` on the stage, such as ``split.none``, on the left half. It
makes the left half the whole scan again and deletes the right half, with its versions and files, and so the work on
it. A run that was not confirmed by the user leaves the page failed with the reason and deletes nothing. The deletion
is stored with the new current version of the page, in the work of the caller's ``change_book``, so a replacement that
fails leaves the right half in place, and its files are removed after the transaction.

The reads and writes of the book are synchronous and go through the repositories of the work that calls them, which is
how a caller of ``undoing`` and ``unsplit`` joins its own transaction. The class announces the versions it made ready
after the transaction, which a preview waits for.
"""

import logging
from contextlib import AsyncExitStack, suppress
from typing import TYPE_CHECKING
from uuid import uuid4

from attrs import evolve, frozen

from bookreviver.domain.entities import Page, PageVersion, VersionInputs
from bookreviver.domain.enums import (
    PageOrigin,
    ProcessorScope,
    RunOutcome,
    Side,
    Stage,
    VersionData,
    VersionScale,
    VersionState,
)
from bookreviver.domain.errors import ConflictError, DomainError, NotFoundError
from bookreviver.domain.events import PageVersionReady
from bookreviver.domain.geometry import SplitChoice
from bookreviver.domain.ids import PageId
from bookreviver.domain.keys import BookKeys
from bookreviver.domain.values import PageStageKey, PageStepKey
from bookreviver.services.page_labels import PageLabels
from bookreviver.services.step_params import PageLayers
from bookreviver.services.steps import StepRun

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.entities import BookRecord, PageEdit, PageStepState, Recipe
    from bookreviver.domain.values import MetadataMap
    from bookreviver.ports.persistence import BookRepositories, UnitOfWork
    from bookreviver.ports.processing import Processor, StepOutput
    from bookreviver.services.stage_records import StageRecords
    from bookreviver.services.stage_runs import StageRuntime, StepSource

UNSPLIT_NOT_CONFIRMED: str = (
    'Undoing the split deletes page {page_id}, the right half of the spread, with its versions. Confirm it to go on.'
)
WRONG_HALVES: str = 'The step {key} made {count} outputs, and a split into the halves of a spread makes two.'
UNEXPECTED_FAILURE: str = 'The split failed because of an unexpected error. It has been logged.'
NO_SCAN: str = 'The page {page_id} has no scan to split.'

logger = logging.getLogger(__name__)


@frozen(kw_only=True)
class Halves:
    """The two pages of a spread as a split will leave them, before any of them is stored.

    :ivar left: The page that runs the step, as the left half.
    :ivar right: The right half, which is a new page when the scan was not split before.
    :ivar left_changes: Whether the left page changes, since it was the whole scan.
    :ivar right_is_new: Whether the right half is a page to add.
    """

    left: Page
    right: Page
    left_changes: bool
    right_is_new: bool


@frozen(kw_only=True)
class SplitBasis:
    """What a split read before its step ran, which the work that stores the halves reads again.

    :ivar halves: The two pages of the spread as they were worked out.
    :ivar params: Parameters of the step after the layers of the page were laid over the recipe and the check.
    :ivar templates: The versions of the left and the right half, running and with no files yet.
    :ivar stored: The version of each half's identifier an earlier run stored, or None.
    :ivar recipe: The recipe whose first step splits, which both halves record as the one that made them.
    :ivar state_key: Key of the settings and the cut line of the step on the page.
    :ivar state: The settings and the cut line as they were read, or None for a step the page has none for.
    """

    halves: Halves
    params: MetadataMap
    templates: Sequence[PageVersion]
    stored: Sequence[PageVersion | None]
    recipe: Recipe
    state_key: PageStepKey
    state: PageStepState | None


@frozen(kw_only=True)
class Unsplit:
    """A split that is undone: the left half that becomes the whole scan, and the right halves it deletes.

    :ivar page: The left half, as read.
    :ivar rights: The pages of the scan that come after it, which are deleted.
    """

    page: Page
    rights: Sequence[Page]

    def deletes_as(self, other: Unsplit | None) -> bool:
        """Tell whether another undoing, read later, deletes the same right halves.

        :param other: The undoing as it reads now, or None when the page undoes no split any more.
        :type other: Unsplit | None
        :returns: True when ``other`` exists and its right halves are the right halves of this one.
        :rtype: bool
        """
        return other is not None and {right.id for right in other.rights} == {right.id for right in self.rights}


class SpreadSplit:
    """Applies a step that splits a scan to the page of the scan, and undoes the split."""

    def __init__(
        self, *, book_record: BookRecord, uow: UnitOfWork, runtime: StageRuntime, records: StageRecords
    ) -> None:
        """Work over the ports of one job.

        :param book_record: Record of the book, whose image policy chooses the format of a version's ``full``.
        :type book_record: BookRecord
        :param uow: Unit of work, whose ``change_book`` stores a split once it is complete.
        :type uow: UnitOfWork
        :param runtime: The runner, the catalogue, the publisher, the clock and the order keys.
        :type runtime: StageRuntime
        :param records: Writer of the stage records.
        :type records: StageRecords
        """
        self._book_record = book_record
        self._uow = uow
        self._runner = runtime.runner
        self._catalogue = runtime.catalogue
        self._publisher = runtime.publisher
        self._clock = runtime.clock
        self._order_keys = runtime.order_keys
        self._records = records
        self._labels = PageLabels(clock=runtime.clock)
        self._keys = BookKeys(book_record.id)

    def splits(self, recipe: Recipe) -> bool:
        """Tell whether a recipe splits scans: it is a recipe of the page split whose step makes a part for each half.

        :param recipe: The recipe of the stage.
        :type recipe: Recipe
        :returns: True when the first step of the recipe that is switched on is of the scope ``split``.
        :rtype: bool
        :raises NotFoundError: If the processor of the step is not in the catalogue.
        """
        steps = recipe.enabled_steps
        if recipe.stage is not Stage.PAGE_SPLIT or not steps:
            return False
        return self._catalogue.get(steps[0].processor_key).spec.scope is ProcessorScope.SPLIT

    async def split(self, page: Page, recipe: Recipe, source: StepSource, *, confirmed: bool = False) -> RunOutcome:
        """Split the scan of a page into two pages, and make the base version of each half the current one.

        A step that decides for each scan, such as ``split.auto``, may keep the scan whole and make one output. The
        page then is the whole scan, and a spread it was split into before is undone as by a step of the scope ``page``:
        the right half is deleted, which needs the confirmation of the user.

        Called outside any transaction, and it opens its own: one reads what the split goes by, one stores the split
        after the step ran, and one stores a failed version when the step failed. The second reads again what the split
        went by: the page, the halves of its scan, the settings and the cut line of the step, and the recipe. A split
        whose basis changed meanwhile is skipped and writes nothing, and the files the step wrote are discarded.

        :param page: The page that runs the step, which becomes the left half.
        :type page: Page
        :param recipe: The recipe whose first step splits the scan, which both halves record as the one that made them.
        :type recipe: Recipe
        :param source: The scan the step reads.
        :type source: StepSource
        :param confirmed: Whether the user confirmed that keeping the scan whole deletes the right half of a spread.
        :type confirmed: bool
        :returns: Done when the pages are made and current, failed when the step failed, which leaves the book as it
                  was and a failed version on the page, and skipped when the page was deleted or what the split went by
                  changed meanwhile.
        :rtype: RunOutcome
        :raises NotFoundError: If the processor is not in the catalogue.
        :raises InvalidParametersError: If the parameters do not fit the processor.
        :raises ConflictError: If the page has no scan.
        """
        step = recipe.enabled_steps[0]
        processor = self._catalogue.get(step.processor_key)
        layers = PageLayers(page)

        def read(repos: BookRepositories) -> SplitBasis:
            """Read the settings and the cut line of the step, the halves of the scan, and the versions made before.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: What the split goes by.
            :rtype: SplitBasis
            :raises InvalidParametersError: If the parameters do not fit the processor.
            :raises ConflictError: If the page has no scan.
            """
            state_key = PageStepKey(page.id, Stage.PAGE_SPLIT, step.step_id)
            state = repos.page_step_states.find(state_key)
            params = processor.validate_params(
                layers.lay(repos, step.step_id, step.params, None if state is None else state.params)
            )
            halves = self._halves(repos, page)
            templates = [
                self._template(half, processor, params, None if state is None else state.edit)
                for half in (halves.left, halves.right)
            ]
            return SplitBasis(
                halves=halves,
                params=params,
                templates=templates,
                stored=[repos.page_versions.find(template.id) for template in templates],
                recipe=recipe,
                state_key=state_key,
                state=state,
            )

        basis = await self._uow.read_book(self._book_record.id, read)
        run = StepRun(
            processor_key=step.processor_key,
            params=basis.params,
            input_data=source.data,
            image=source.image,
            edit=None if basis.state is None else basis.state.edit,
        )
        try:
            made = await self._cached(basis.stored)
            if made is None:
                made = await self._make(basis.templates, run)
            undoing = (
                await self._uow.read_book(
                    self._book_record.id, lambda repos: self.undoing(repos, page, recipe, confirmed=confirmed)
                )
                if len(made) == 1
                else None
            )
        except DomainError as error:
            await self._fail(basis.templates[0], str(error))
            return RunOutcome.FAILED
        except Exception:
            logger.exception('The split %s of page %s failed', run.processor_key, page.id)
            await self._fail(basis.templates[0], UNEXPECTED_FAILURE)
            return RunOutcome.FAILED
        if not await self._commit(basis, made, undoing):
            return RunOutcome.SKIPPED
        await self._announce_versions(basis, made)
        if undoing is not None:
            await self.finish_unsplit(undoing)
        return RunOutcome.DONE

    def undoing(self, repos: BookRepositories, page: Page, recipe: Recipe, *, confirmed: bool) -> Unsplit | None:
        """Find what undoing a split deletes, when a recipe that does not split runs on a left half.

        Nothing is written. The deletion is made by ``unsplit``, with the version that replaces the split. It reads
        through the repositories of the transaction of its caller, which may be a ``read`` or the work of a
        ``change_book``.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param page: The page that runs the recipe.
        :type page: Page
        :param recipe: The recipe of the stage.
        :type recipe: Recipe
        :param confirmed: Whether the user confirmed that the right half is deleted.
        :type confirmed: bool
        :returns: The undoing, or None when the recipe is of another stage, splits scans, or the page is no left half.
        :rtype: Unsplit | None
        :raises ConflictError: If a right half is to be deleted and the user did not confirm it.
        """
        if recipe.stage is not Stage.PAGE_SPLIT or page.slot != Page.LEFT_HALF or page.scan_id is None:
            return None
        rights = [sibling for sibling in repos.pages.list_for_scan(page.scan_id) if sibling.slot > page.slot]
        if rights and not confirmed:
            raise ConflictError(UNSPLIT_NOT_CONFIRMED.format(page_id=rights[0].id))
        return Unsplit(page=page, rights=rights)

    def unsplit(self, repos: BookRepositories, undoing: Unsplit) -> None:
        """Delete the right halves and make the left half the whole scan, in the work of the caller's ``change_book``.

        The pages after the deleted halves move up, so those that turn over to the other side of the book are marked
        stale where the odd pages or the even pages have values for a step. Never opens a transaction. The left half is
        the page the caller read inside its work, so the write keeps whatever changed it since the run began.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param undoing: What the undoing deletes.
        :type undoing: Unsplit
        """
        self._labels.settle(repos, leaving=[right.id for right in undoing.rights])
        with self._records.watching_sides(repos):
            for right in undoing.rights:
                repos.pages.delete(right.id)
        repos.pages.update(evolve(undoing.page, slot=Page.WHOLE_SCAN, updated_at=self._clock.now()))

    async def finish_unsplit(self, undoing: Unsplit) -> None:
        """Remove the files of the deleted pages, after the transaction of the caller ended.

        :param undoing: What the undoing deleted.
        :type undoing: Unsplit
        """
        for right in undoing.rights:
            await self._runner.discard(self._keys.page(right.id))

    def _halves(self, repos: BookRepositories, page: Page, right_id: PageId | None = None) -> Halves:
        """Work out the two pages of the spread, without storing any of them.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param page: The page that runs the step.
        :type page: Page
        :param right_id: Identifier a new right half takes, when the versions of the halves were made under it before,
                         or None for a new one.
        :type right_id: PageId | None
        :returns: The left half and the right half, the right one new when the scan has none yet.
        :rtype: Halves
        :raises ConflictError: If the page has no scan.
        """
        if page.scan_id is None:
            raise ConflictError(NO_SCAN.format(page_id=page.id))
        left = page if page.slot == Page.LEFT_HALF else evolve(page, slot=Page.LEFT_HALF, updated_at=self._clock.now())
        siblings = repos.pages.list_for_scan(page.scan_id)
        if (right := next((sibling for sibling in siblings if sibling.slot == Page.RIGHT_HALF), None)) is not None:
            return Halves(left=left, right=right, left_changes=left != page, right_is_new=False)
        upper = repos.pages.neighbour_key(left.order_key, Side.AFTER)
        moment = self._clock.now()
        right = Page(
            id=PageId(uuid4()) if right_id is None else right_id,
            order_key=self._order_keys.between(lower=left.order_key, upper=upper),
            origin=PageOrigin.SCAN,
            scan_id=page.scan_id,
            slot=Page.RIGHT_HALF,
            created_at=moment,
            updated_at=moment,
        )
        return Halves(left=left, right=right, left_changes=left != page, right_is_new=True)

    def _template(self, half: Page, processor: Processor, params: MetadataMap, edit: PageEdit | None) -> PageVersion:
        """Build the version a half would have, which is running and has no files yet.

        :param half: The page of the half.
        :type half: Page
        :param processor: The processor that splits the scan.
        :type processor: Processor
        :param params: Parameters of the step after the check.
        :type params: MetadataMap
        :param edit: Manual cut line of the step, or None.
        :type edit: PageEdit | None
        :returns: The version, whose identifier follows from what it depends on.
        :rtype: PageVersion
        """
        edit_hash = '' if edit is None else edit.edit_hash
        inputs = VersionInputs(
            page_id=half.id, processor=processor.spec.ref, params=params, input_id=None, edit_hash=edit_hash
        )
        return PageVersion(
            id=inputs.identify(),
            page_id=half.id,
            stage=Stage.PAGE_SPLIT,
            processor=processor.spec.ref,
            params=params,
            renditions=None,
            state=VersionState.RUNNING,
            scale=VersionScale.FULL,
            edit_hash=edit_hash,
            created_at=self._clock.now(),
        )

    async def _cached(self, stored: Sequence[PageVersion | None]) -> list[PageVersion] | None:
        """Find the versions an earlier run made of the same scan, step, parameters and edit.

        A run that kept the scan whole made one version, so the right half has none and the run is still complete.

        :param stored: The version of each half's identifier an earlier run stored, or None.
        :type stored: Sequence[PageVersion | None]
        :returns: The versions, with the pyramid of each cut, or None when the step has to run.
        :rtype: list[PageVersion] | None
        """
        left = stored[0]
        if left is None or left.state is not VersionState.READY:
            return None
        count = SplitChoice.ONE_PAGE if left.data.get(VersionData.PAGES) == SplitChoice.ONE_PAGE else len(stored)
        wanted = [version for version in stored[:count] if version is not None]
        if len(wanted) != count or any(version.state is not VersionState.READY for version in wanted):
            return None
        return [await self._tiled(version) for version in wanted]

    async def _make(self, templates: Sequence[PageVersion], run: StepRun) -> list[PageVersion]:
        """Run the step once and store the files of each page it made, with the tile pyramid.

        :param templates: The versions of the left and the right half.
        :type templates: Sequence[PageVersion]
        :param run: What the step reads.
        :type run: StepRun
        :returns: The versions as ready, two for a scan that was split and one for a scan kept whole.
        :rtype: list[PageVersion]
        :raises ConflictError: If the step makes neither one output nor two.
        :raises DomainError: If the step cannot run on this input.
        """
        async with self._runner.execute(run) as result:
            outputs: list[StepOutput] = list(result.outputs)
            if not 0 < len(outputs) <= len(templates):
                raise ConflictError(WRONG_HALVES.format(key=run.processor_key, count=len(outputs)))
            return [
                await self._runner.store(
                    self._keys, template, output, policy=self._book_record.image_policy, tiles=True
                )
                for template, output in zip(templates, outputs, strict=False)
            ]

    async def _fail(self, template: PageVersion, reason: str) -> None:
        """Store a failed version on the page that ran the step, which tells the user why, and nothing else.

        Called outside any transaction, and it opens a ``change_book`` of its own, in which the page and the version of
        that identifier are read again. A page that was deleted meanwhile gets nothing.

        :param template: The version the left half would have.
        :type template: PageVersion
        :param reason: Why the step failed.
        :type reason: str
        """

        def work(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Store the failed version, unless the page is gone.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book as the change finds it, which the work does not need.
            :type _book_record: BookRecord
            :raises NotFoundError: If the page was deleted.
            """
            repos.pages.get(template.page_id)
            stored = repos.page_versions.find(template.id)
            failed = evolve(
                template if stored is None else stored, state=VersionState.FAILED, data={VersionData.ERROR: reason}
            )
            if stored is None:
                repos.page_versions.add(failed)
            else:
                repos.page_versions.update(failed)

        # A page that was deleted meanwhile has no version to tell the reason on, and the run reports it as skipped
        with suppress(NotFoundError):
            await self._uow.change_book(self._book_record.id, work)

    async def _tiled(self, version: PageVersion) -> PageVersion:
        """Cut the tile pyramid of a half that an earlier run made, if it was never cut.

        :param version: A ready version of a half.
        :type version: PageVersion
        :returns: The version with its pyramid cut.
        :rtype: PageVersion
        """
        return version if version.tiles_ready else await self._runner.cut_tiles(self._keys, version)

    async def _commit(self, basis: SplitBasis, made: Sequence[PageVersion], undoing: Unsplit | None) -> bool:
        """Store the pages, the versions and the heads of a split in one change, if what the split went by still holds.

        Called outside any transaction, and it opens a ``change_book`` of its own, in whose work the page, the halves
        of its scan, the settings and the cut line of the step, and the recipe are read again. A split whose page was
        deleted meanwhile, whose scan got another right half, or whose step, edit, recipe or undoing changed, stores
        nothing. The pages are written as the work reads them, so a move of the page since the step began is kept. The
        files of the versions the step made are discarded when nothing is stored, and when the change fails.

        A scan that was split gets the new page, the change of the left page, and the versions and heads of both halves.
        A scan kept whole gets the version and head of its page, and the undoing of an earlier split.

        :param basis: What the split read before its step ran.
        :type basis: SplitBasis
        :param made: The versions of the left and the right half, ready, or of the whole page alone.
        :type made: Sequence[PageVersion]
        :param undoing: The earlier split that keeping the scan whole undoes, or None.
        :type undoing: Unsplit | None
        :returns: Whether the split was stored, which it is not when what the split went by changed.
        :rtype: bool
        """

        def work(repos: BookRepositories, _book_record: BookRecord) -> bool:
            """Store the split, unless the page is gone or what the split went by changed.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book as the change finds it, which the work does not need.
            :type _book_record: BookRecord
            :returns: Whether the split was stored.
            :rtype: bool
            """
            try:
                fresh = repos.pages.get(basis.halves.left.id)
            except NotFoundError:
                return False
            halves = self._halves(repos, fresh, right_id=basis.halves.right.id)
            again = None if undoing is None else self.undoing(repos, fresh, basis.recipe, confirmed=True)
            if (
                (halves.right.id, halves.right_is_new) != (basis.halves.right.id, basis.halves.right_is_new)
                or repos.page_step_states.find(basis.state_key) != basis.state
                or repos.recipes.get(basis.recipe.id).updated_at != basis.recipe.updated_at
            ) or (undoing is not None and not undoing.deletes_as(again)):
                return False
            self._write(repos, halves, made, basis.recipe, again)
            return True

        async with AsyncExitStack() as cleanup:
            # What the step wrote belongs to no stored version unless the change commits, except the versions an
            # earlier run made ready
            for version, earlier in zip(made, basis.stored, strict=False):
                if earlier is None or earlier.state is not VersionState.READY:
                    cleanup.push_async_callback(self._runner.discard, self._keys.version_directory(version))
            stored = await self._uow.change_book(self._book_record.id, work)
            if stored:
                cleanup.pop_all()
        return stored

    def _write(
        self,
        repos: BookRepositories,
        halves: Halves,
        made: Sequence[PageVersion],
        recipe: Recipe,
        undoing: Unsplit | None,
    ) -> None:
        """Write the pages, the versions and the heads of a split, in the work of the ``change_book`` of ``_commit``.

        Never opens a transaction. The versions are written by what the work reads of them, so the notes of the user on
        a result that is made again stay with it.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param halves: The two pages of the spread as the work reads them.
        :type halves: Halves
        :param made: The versions of the left and the right half, ready, or of the whole page alone.
        :type made: Sequence[PageVersion]
        :param recipe: The recipe that made the pages.
        :type recipe: Recipe
        :param undoing: The earlier split that keeping the scan whole undoes, with the page as the work reads it, or
                        None.
        :type undoing: Unsplit | None
        """
        pages = (halves.left, halves.right)[: len(made)]
        # The new half moves the pages after it on, which turns some of them over to the other side of the book
        with self._records.watching_sides(repos):
            if len(made) > 1 and halves.right_is_new:
                repos.pages.add(halves.right)
        if len(made) > 1 and halves.left_changes:
            repos.pages.update(halves.left)
        if len(made) > 1 and halves.right_is_new:
            # The new half takes a number, which may be past what its section can write
            self._labels.settle(repos)
        for version in made:
            if (earlier := repos.page_versions.find(version.id)) is None:
                repos.page_versions.add(version)
            else:
                # The notes of the user on the result stay with it when it is made again
                repos.page_versions.update(evolve(version, mark=earlier.mark, comment=earlier.comment))
        for half, version in zip(pages, made, strict=True):
            self._records.set_head(
                repos, PageStageKey(half.id, Stage.PAGE_SPLIT), head_version_id=version.id, recipe_id=recipe.id
            )
        if undoing is not None:
            self.unsplit(repos, undoing)

    async def _announce_versions(self, basis: SplitBasis, made: Sequence[PageVersion]) -> None:
        """Announce the versions the split made ready, which a preview waits for, after the transaction.

        :param basis: What the split read before its step ran, which holds the versions an earlier run stored, which
                      were not made again when ready.
        :type basis: SplitBasis
        :param made: The versions of the left and the right half, ready, or of the whole page alone.
        :type made: Sequence[PageVersion]
        """
        for version, earlier in zip(made, basis.stored, strict=False):
            if earlier is None or earlier.state is not VersionState.READY:
                await self._publisher.publish(PageVersionReady(book_id=self._book_record.id, version=version))
