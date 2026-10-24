"""The stages of a book as a whole: the summary every stage bar draws from, and the pages of one stage for its strip.

The recipes, the runs and the previews of a stage live in the processing router. These two routes only read, and each
answers for the whole book in one request, so the interface never asks page by page.
"""

from dataclasses import dataclass
from functools import partial
from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends, Query, Request
from fastapi_pagination import Page, Params

from bookreviver.api.access import open_book
from bookreviver.api.freshness import fresh_book
from bookreviver.api.pagination import ManifestPage, ManifestParams, Pager
from bookreviver.api.routers.processing import StagePath
from bookreviver.api.schemas.stages import StagePageSchema, StageSummarySchema
from bookreviver.domain.ids import StepId
from bookreviver.domain.stage_summaries import StageRow, StageSummary
from bookreviver.services.book import Book
from bookreviver.services.stage_summaries import StageSummaries


@dataclass(frozen=True)
class StageRowsPath(StagePath):
    """The address of the rows of a stage, with the request, whose application knows the route that serves the images.

    :ivar request: The request.
    """

    request: Request


router = APIRouter(prefix='/books', tags=['stages'], route_class=DishkaRoute, dependencies=[Depends(fresh_book)])


@router.get('/{book_id}/stages')
async def list_stages(
    book: Annotated[Book, Depends(open_book)],
    params: Annotated[Params, Depends()],
    stages: FromDishka[StageSummaries],
) -> Page[StageSummarySchema]:
    """List the stages of a book in the order of the pipeline, each summed over the pages of the book.

    A stage is available when it is done by hand or a processor of it is installed. The counts of a stage done by hand
    are zero.

    \N{FORM FEED}
    :param book: The book the path names, open for the signed-in account.
    :type book: Book
    :param params: Page number and size from the query; the ten stages fit one page of the default size.
    :type params: Params
    :param stages: Stage summaries of the request.
    :type stages: StageSummaries
    :returns: One page of the stages.
    :rtype: Page[StageSummarySchema]
    """
    pager = Pager[StageSummary, StageSummarySchema](params, StageSummarySchema.model_validate)
    return pager.page(await stages.stages(book, pager.request))


@router.get('/{book_id}/stages/{stage}/pages')
async def list_stage_pages(
    book: Annotated[Book, Depends(open_book)],
    address: Annotated[StageRowsPath, Depends()],
    params: Annotated[ManifestParams, Depends()],
    stages: FromDishka[StageSummaries],
    step: Annotated[StepId | None, Query(description='A step of a recipe of the stage to place every page at')] = None,
) -> ManifestPage[StagePageSchema]:
    """List the pages of a book in book order, each with where it stands in a stage and the version that is its result.

    A page the stage has not run on has the status ``not-run`` and no version. A page holds up to a thousand rows, as
    the page manifest does, so a strip of a whole book takes few requests. With ``step`` every row also says what that
    step read and made on its page and where the shape of the step comes from; a step no recipe of the stage has is a
    404. Every row has a ``picture``, the version that stands for the page at the place asked for: the version the step
    reads, or the last version of the stage before it for a page that has not come as far, or else what the stage reads;
    without ``step`` the current version of the stage, else what the stage reads. A strip and a canvas draw it alike.

    \N{FORM FEED}
    :param book: The book the path names, open for the signed-in account.
    :type book: Book
    :param address: Identifiers of the book and the stage, and the request.
    :type address: StageRowsPath
    :param params: Page number and size from the query, the size up to a thousand.
    :type params: ManifestParams
    :param stages: Stage summaries of the request.
    :type stages: StageSummaries
    :param step: The step to place every page at, or None for the rows of the stage alone.
    :type step: StepId | None
    :returns: One page of the rows of the stage.
    :rtype: ManifestPage[StagePageSchema]
    """
    to_schema = partial(StagePageSchema.of, book_id=book.id, request=address.request)
    pager = Pager[StageRow, StagePageSchema](params, to_schema)
    return pager.page(await stages.rows(book, address.stage, pager.request, step))
