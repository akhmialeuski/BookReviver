"""The history of a step on a page, and the undo that takes its changes back.

Every change of a layer of a step on a page is in the history, written by the use case that made it. An undo is a use
case of its own: it writes the content a change replaced back into the layer, as a new change whose source is an undo
and which names the change it takes back, so the history only grows and shows what the undo did. Taking a change back
needs the layer to hold what the change left in it, since writing ``before`` over a layer that has changed since would
lose the later change. A change of a batch is taken back with the rest of its batch, on every page the batch reached,
and the undos of one batch share a batch of their own, so the undo of a batch is one action as well.

Taking back marks the stage of each page that changed stale and processes nothing, like the changes it undoes.

A clear is the one way a history shrinks: it takes the settings and the edit of a step away from one page, deletes that
step's history on that page, and deletes the results of the step on the page with every result that read them, so the
step is back to the state it had before it first ran or changed there, and nothing of it can be undone any more. Other
pages and the steps that did not read the cleared one are untouched.
"""

import logging
from typing import TYPE_CHECKING

from bookreviver.domain.errors import ConflictError
from bookreviver.domain.keys import BookKeys
from bookreviver.services.processing_parts import BOOK_BUSY
from bookreviver.services.step_history import StepOnPage

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.core.book import Book
    from bookreviver.domain.history import ClearedStep, PageHistoryEntry, StepHistory
    from bookreviver.domain.ids import PageHistoryEntryId
    from bookreviver.domain.page_steps import PageStepKey
    from bookreviver.services.processing_parts import JobStarter
    from bookreviver.services.stage_records import StageRecords

logger = logging.getLogger(__name__)


class PageHistoryService:
    """Lists the history of the steps of the acting account's pages, takes its changes back, and clears a step."""

    def __init__(self, *, records: StageRecords, starter: JobStarter) -> None:
        """Work over the ports of one request.

        :param records: Writer of the stage records, which an undo marks stale.
        :type records: StageRecords
        :param starter: Queuer of the jobs, which cuts the pyramid of the version a clear makes current.
        :type starter: JobStarter
        """
        self._records = records
        self._starter = starter

    async def list(self, book: Book, key: PageStepKey) -> StepHistory:
        """Return the history of one step on one page.

        :param book: The open book.
        :type book: Book
        :param key: The page, the stage and the step.
        :type key: PageStepKey
        :returns: The changes of the step, oldest first, with which of them stand.
        :rtype: StepHistory
        :raises NotFoundError: If the book has no such page.
        """
        records = self._records
        return await book.read(lambda repos: StepOnPage(records, repos, key).history())

    async def undo(
        self, book: Book, key: PageStepKey, change_id: PageHistoryEntryId | None
    ) -> Sequence[PageHistoryEntry]:
        """Take back the newest change of a step on a page, or every change back to a chosen one.

        A change of a batch brings the rest of its batch with it, whatever page the rest is on.

        :param book: The open book.
        :type book: Book
        :param key: The page, the stage and the step.
        :type key: PageStepKey
        :param change_id: The oldest change to take back, which takes back every change after it as well, or None for
                          the newest change alone.
        :type change_id: PageHistoryEntryId | None
        :returns: The undos that were written, which are none when the step has no change to take back.
        :rtype: Sequence[PageHistoryEntry]
        :raises NotFoundError: If the book has no such page, or the step has no standing change with this identifier.
        :raises ConflictError: If a layer changed after the change that is taken back, which a later change of the
                               same page or of a page of the batch did.
        """
        records = self._records
        return await book.change(lambda repos, _book_record: StepOnPage(records, repos, key).undo(change_id))

    async def clear(self, book: Book, key: PageStepKey) -> ClearedStep:
        """Return a step to its initial state on a page: delete its history, its settings, its edit and its results.

        The results of the step on the page go with the versions that read them, directly or through a chain, whatever
        their stage, and with the marks and comments of those versions. The stage of the page then stands on the version
        the step read, marked stale, or has no current version when the step is the first of its recipe. A later stage
        whose current version went has no record any longer, and the later stages of the page are marked stale. The
        versions of other pages, of steps that did not read the step, and the changes of a batch that reached other
        pages stay, and an undo of such a batch goes on working, since it takes back only the changes it finds.

        The version the stage stands on was made by a step in the middle of the recipe, which cuts no pyramid, and the
        page is shown by the pyramid of the current version of its stage. So the cutting of that pyramid is queued once
        the transaction of the clear has ended, which a book busy with another tile cutting or collection leaves
        undone.

        A preview of the book, queued or running, is cancelled first, since the reader asked for the clear and a
        preview is disposable: the editor of a step asks for one by itself when the step is opened, so a clear a moment
        later would otherwise be refused by a job the reader never started. It writes versions of the preview scale
        only, and its last write finds the job cancelled and changes nothing, as for a run.

        The cancelling and the queuing of the tile cutting are transactions of their own, so this use case opens one
        ``Book.change`` for the rows. The files of the deleted versions are removed after it ended, so a store that
        fails to remove them leaves them without a row for the next collection and does not fail the clear.

        :param book: The open book.
        :type book: Book
        :param key: The page, the stage and the step.
        :type key: PageStepKey
        :returns: The number of changes deleted and the versions deleted, none of which when the step had nothing on the
                  page.
        :rtype: ClearedStep
        :raises NotFoundError: If the book has no such page, or no recipe of the stage has the step.
        :raises ConflictError: If a run, a measure of the book, a tile cutting or a collection of the book is queued
                               or running, which may be reading or deleting the versions the clear deletes.
        """
        # The step is checked before the previews are cancelled, so a request the change would refuse cancels nothing
        records = self._records
        await book.read(lambda repos: StepOnPage(records, repos, key).recipes())
        if await self._starter.free_of_previews(book):
            raise ConflictError(BOOK_BUSY)

        cleared, untiled = await book.change(lambda repos, _book_record: StepOnPage(records, repos, key).clear())
        await self._starter.enqueue_tiles(book, untiled)
        for version in cleared.versions:
            try:
                await book.assets.delete_prefix(BookKeys.version_directory(version))
            except OSError:
                logger.exception('The files of the version %s were not removed after its page was cleared', version.id)
        return cleared
