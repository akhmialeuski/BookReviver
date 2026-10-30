"""The catalogue of processors: what a recipe can be made of, and the form of each one's parameters.

``GET /processors`` lists the processors the application can run, each with the JSON Schema of its parameters,
from which the interface builds the settings form. The catalogue is the same for every account, so the route asks for
no book, and only the guard of the API keeps out a caller who is not signed in.
"""

from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends
from fastapi_pagination import Page, Params

from bookreviver.api.pagination import Pager
from bookreviver.api.schemas.processing import ProcessorSchema
from bookreviver.domain.processors import ProcessorSpec
from bookreviver.domain.slices import Slice
from bookreviver.services.processing import ProcessingService

router = APIRouter(prefix='/processors', tags=['processing'], route_class=DishkaRoute)


@router.get('')
async def list_processors(
    params: Annotated[Params, Depends()], processing: FromDishka[ProcessingService]
) -> Page[ProcessorSchema]:
    """List the processors a recipe can use, by key, each with the JSON Schema of its parameters.

    \N{FORM FEED}
    :param params: Page number and size from the query.
    :type params: Params
    :param processing: Processing service of the request.
    :type processing: ProcessingService
    :returns: One page of the processors.
    :rtype: Page[ProcessorSchema]
    """
    pager = Pager[ProcessorSpec, ProcessorSchema](params, ProcessorSchema.model_validate)
    specs = processing.processors()
    window = specs[pager.request.offset : pager.request.offset + pager.request.limit]
    return pager.page(Slice(items=window, total=len(specs)))
