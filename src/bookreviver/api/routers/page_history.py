"""The history of a step on a page, and the undo that takes its changes back.

The history lists every change of the step on the page, the newest first, and tells which of them were taken back. An
undo takes back the newest change that stands, or every change back to a chosen one. It writes the undos as new changes
and marks the stage of the page stale, and processes nothing. A clear deletes the history of the step on the page, takes
its settings and its edit away, and deletes its results with the results that read them. It is a job of the book: the
route answers 202 with it, and the job waits behind the jobs the book has queued or running.
"""

from dataclasses import dataclass
from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends, Path, status
from fastapi_pagination import Page, Params

from bookreviver.api.access import BOOK_ID_DESCRIPTION, AccessDep
from bookreviver.api.freshness import fresh_book
from bookreviver.api.pagination import Pager
from bookreviver.api.schemas.jobs import JobSchema
from bookreviver.api.schemas.page_history import PageHistoryEntrySchema, UndoForm, UndoneSchema
from bookreviver.domain.history import PageHistoryEntry
from bookreviver.domain.ids import BookId, PageId, StepId
from bookreviver.domain.page_steps import PageStepKey
from bookreviver.domain.slices import Slice
from bookreviver.domain.stages import Stage
from bookreviver.services.page_history import PageHistoryService

router = APIRouter(prefix='/books', tags=['page-history'], route_class=DishkaRoute, dependencies=[Depends(fresh_book)])


@dataclass(frozen=True)
class StepPath:
    """The identifiers in the address of the history of one step on one page.

    :ivar page_id: Identifier of the page.
    :ivar stage: Stage of the step.
    :ivar step_id: Identifier of the step of a recipe.
    """

    page_id: Annotated[PageId, Path(description='Identifier of the page')]
    stage: Annotated[Stage, Path(description='Stage of the step')]
    step_id: Annotated[StepId, Path(description='Identifier of the step of a recipe')]

    @property
    def key(self) -> PageStepKey:
        """The key the state of the step on the page is stored under."""
        return PageStepKey(self.page_id, self.stage, self.step_id)


@router.get('/{book_id}/pages/{page_id}/history/{stage}/{step_id}')
async def list_history(
    access: AccessDep,
    book_id: Annotated[BookId, Path(description=BOOK_ID_DESCRIPTION)],
    address: Annotated[StepPath, Depends()],
    params: Annotated[Params, Depends()],
    history: FromDishka[PageHistoryService],
) -> Page[PageHistoryEntrySchema]:
    """List the changes of a step on a page, the newest first, with which of them were taken back.

    \N{FORM FEED}
    :param access: Access of the signed-in account, which opens the book the path names.
    :type access: Access
    :param book_id: Identifier of the book, from the path.
    :type book_id: BookId
    :param address: Identifiers of the book, the page, the stage and the step.
    :type address: StepPath
    :param params: Page number and size from the query.
    :type params: Params
    :param history: Page history service of the request.
    :type history: PageHistoryService
    :returns: One page of the changes.
    :rtype: Page[PageHistoryEntrySchema]
    """
    book = await access.book(book_id)
    found = await history.list(book, address.key)
    undone = found.undone

    def to_schema(change: PageHistoryEntry) -> PageHistoryEntrySchema:
        """Build the schema of one change, marked when a later change took it back.

        :param change: The change.
        :type change: PageHistoryEntry
        :returns: The schema.
        :rtype: PageHistoryEntrySchema
        """
        return PageHistoryEntrySchema.model_validate(change).model_copy(update={'undone': change.id in undone})

    pager = Pager[PageHistoryEntry, PageHistoryEntrySchema](params, to_schema)
    newest_first = found.changes[::-1]
    window = newest_first[pager.request.offset : pager.request.offset + pager.request.limit]
    return pager.page(Slice(items=window, total=len(newest_first)))


@router.post('/{book_id}/pages/{page_id}/history/{stage}/{step_id}/undo')
async def undo_change(
    access: AccessDep,
    book_id: Annotated[BookId, Path(description=BOOK_ID_DESCRIPTION)],
    address: Annotated[StepPath, Depends()],
    form: UndoForm,
    history: FromDishka[PageHistoryService],
) -> UndoneSchema:
    """Take back the newest change of a step on a page, or every change back to a chosen one, and mark the stage stale.

    A change of a batch is taken back with the rest of its batch, on every page the batch reached. The answer is 409
    when a layer changed after the change that is taken back, and 200 with no changes when there is nothing to take
    back.

    \N{FORM FEED}
    :param access: Access of the signed-in account, which opens the book the path names.
    :type access: Access
    :param book_id: Identifier of the book, from the path.
    :type book_id: BookId
    :param address: Identifiers of the book, the page, the stage and the step.
    :type address: StepPath
    :param form: The oldest change to take back, or none for the newest change alone.
    :type form: UndoForm
    :param history: Page history service of the request.
    :type history: PageHistoryService
    :returns: The undos that were written.
    :rtype: UndoneSchema
    """
    book = await access.book(book_id)
    written = await history.undo(book, address.key, form.change_id)
    return UndoneSchema(changes=[PageHistoryEntrySchema.model_validate(change) for change in written])


@router.delete('/{book_id}/pages/{page_id}/history/{stage}/{step_id}', status_code=status.HTTP_202_ACCEPTED)
async def clear_history(
    access: AccessDep,
    book_id: Annotated[BookId, Path(description=BOOK_ID_DESCRIPTION)],
    address: Annotated[StepPath, Depends()],
    history: FromDishka[PageHistoryService],
) -> JobSchema:
    """Return a step to its initial state on a page in the background, and answer with the queued job.

    The job deletes the history of the step on the page, its settings, its edit and its results: the versions the step
    made on the page and the versions that read them, with their files and their marks. The stage of the page stands
    on the version the step read, marked stale, and has no current version when the step is the first of its recipe.
    The clear writes nothing to the history, so nothing of it can be undone. The changes of a batch on other pages
    stay. A book runs one job at a time in the order they were asked for, so a clear asked for while a run, a measure
    of the book, a tile cutting or a collection is queued or running waits behind it. A preview of the book is
    cancelled. The answer is 404 when no recipe of the stage has the step, and nothing is queued then.

    \N{FORM FEED}
    :param access: Access of the signed-in account, which opens the book the path names.
    :type access: Access
    :param book_id: Identifier of the book, from the path.
    :type book_id: BookId
    :param address: Identifiers of the book, the page, the stage and the step.
    :type address: StepPath
    :param history: Page history service of the request.
    :type history: PageHistoryService
    :returns: The queued job.
    :rtype: JobSchema
    :raises NotFoundError: If the actor has no such book, the book has no such page, or no recipe of the stage
                           has the step.
    """
    book = await access.book(book_id)
    return JobSchema.model_validate(await history.start_clear(book, address.key))
