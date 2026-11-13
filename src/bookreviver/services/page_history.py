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
pages and the steps that did not read the cleared one are untouched. A clear is a job of the book, which waits behind
the jobs the book has queued or running, since they may read or delete the versions it deletes.
"""

import logging
from typing import TYPE_CHECKING

from bookreviver.domain.errors import DomainError
from bookreviver.domain.jobs import JobEnd, JobKind, JobState, StepClearing
from bookreviver.domain.keys import BookKeys
from bookreviver.services.step_history import StepOnPage

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.core.book import Book
    from bookreviver.domain.history import ClearedStep, PageHistoryEntry, StepHistory
    from bookreviver.domain.ids import PageHistoryEntryId
    from bookreviver.domain.jobs import Job
    from bookreviver.domain.page_steps import PageStepKey
    from bookreviver.ports.runtime import JobControl
    from bookreviver.services.job_starter import JobStarter
    from bookreviver.services.stage_records import StageRecords

logger = logging.getLogger(__name__)


class PageHistoryService:
    """Lists the history of the steps of the acting account's pages, takes its changes back, and clears a step."""

    def __init__(self, *, records: StageRecords, starter: JobStarter) -> None:
        """Work over the ports of one request.

        :param records: Writer of the stage records, which an undo marks stale.
        :type records: StageRecords
        :param starter: Queuer of the jobs, which queues a clear and the cutting of the pyramid of the version it
                        makes current.
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

    async def start_clear(self, book: Book, key: PageStepKey) -> Job:
        """Queue a job that returns a step to its initial state on a page, behind the jobs the book has.

        The step is checked first, so a request the clear would refuse queues nothing and cancels nothing. A preview of
        the book, queued or running, is cancelled when the job is put, since the reader asked for the clear and a
        preview is disposable: the editor of a step asks for one by itself when the step is opened.

        :param book: The open book.
        :type book: Book
        :param key: The page, the stage and the step.
        :type key: PageStepKey
        :returns: The queued job.
        :rtype: Job
        :raises NotFoundError: If the book has no such page, or no recipe of the stage has the step.
        """
        records = self._records
        await book.read(lambda repos: StepOnPage(records, repos, key).recipes())
        return await self._starter.enqueue(book, JobKind.CLEAR_STEP, StepClearing(key=key))

    async def clear_step(self, book: Book, job: Job, control: JobControl) -> JobEnd:
        """Run a ``clear-step`` job: the clear of one step on one page, which is one task.

        :param book: The book of the queue the job was taken from.
        :type book: Book
        :param job: The running job.
        :type job: Job
        :param control: What the job reports through, and learns of its cancellation from, after its task.
        :type control: JobControl
        :returns: How the job ended: failed with the reason when the page or the step is gone by the time the job runs,
                  or the job is not valid, else succeeded.
        :rtype: JobEnd
        :raises JobCancelledError: If the job was cancelled while its task ran.
        """
        control.plan(1)
        try:
            await self.clear(book, job.asked(StepClearing).key)
        except DomainError as error:
            return JobEnd(state=JobState.FAILED, error=str(error))
        await control.task_done()
        return JobEnd(state=JobState.SUCCEEDED)

    async def clear(self, book: Book, key: PageStepKey) -> ClearedStep:
        """Return a step to its initial state on a page: delete its history, its settings, its edit and its results.

        It is the work of a ``clear-step`` job, which runs when the jobs put before it have ended. The results of the
        step on the page go with the versions that read them, directly or through a chain, whatever their stage, and
        with the marks and comments of those versions. The stage of the page then stands on the version the step read,
        marked stale, or has no current version when the step is the first of its recipe. A later stage whose current
        version went has no record any longer, and the later stages of the page are marked stale. The versions of other
        pages, of steps that did not read the step, and the changes of a batch that reached other pages stay, and an
        undo of such a batch goes on working, since it takes back only the changes it finds.

        The page and the step are checked again inside the transaction, since they may have gone while the job waited.
        The version the stage stands on was made by a step in the middle of the recipe, which cuts no pyramid, and the
        page is shown by the pyramid of the current version of its stage. So the cutting of that pyramid is put next up
        once the transaction of the clear has ended. The files of the deleted versions are removed after it ended, so a
        store that fails to remove them leaves them without a row for the next collection and does not fail the clear.

        :param book: The open book.
        :type book: Book
        :param key: The page, the stage and the step.
        :type key: PageStepKey
        :returns: The number of changes deleted and the versions deleted, none of which when the step had nothing on the
                  page.
        :rtype: ClearedStep
        :raises NotFoundError: If the book has no such page, or no recipe of the stage has the step.
        """
        records = self._records
        cleared, untiled = await book.change(lambda repos, _book_record: StepOnPage(records, repos, key).clear())
        await self._starter.enqueue_tiles(book, untiled)
        for version in cleared.versions:
            try:
                await book.assets.delete_prefix(BookKeys.version_directory(version))
            except OSError:
                logger.exception('The files of the version %s were not removed after its page was cleared', version.id)
        return cleared
