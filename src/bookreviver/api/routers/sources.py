"""The sources a book was assembled from and their scans, which are read-only records of where the book came from.

Uploading files, which creates sources, is the import route. A source is deleted here, with its files and its scans,
and the pages of the book stay with their own images. That is also how a user gets out of an import that keeps failing
on one scan: the job result names the failed scan, and the source is deleted by hand.
"""

from functools import partial
from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends, Path, Request, status
from fastapi_pagination import Page, Params

from bookreviver.api.auth import ActorDep
from bookreviver.api.pagination import Pager
from bookreviver.api.schemas.sources import ScanQuery, ScanSchema, SourceSchema
from bookreviver.domain.entities import Scan, Source
from bookreviver.domain.ids import ProjectId, SourceId
from bookreviver.services.sources import SourceService

PROJECT_ID_DESCRIPTION: str = 'Identifier of the project'
SOURCE_ID_DESCRIPTION: str = 'Identifier of the source'

router = APIRouter(prefix='/projects', tags=['sources'], route_class=DishkaRoute)


@router.get('/{project_id}/sources')
async def list_sources(
    project_id: Annotated[ProjectId, Path(description=PROJECT_ID_DESCRIPTION)],
    actor: ActorDep,
    params: Annotated[Params, Depends()],
    sources: FromDishka[SourceService],
) -> Page[SourceSchema]:
    """List the sources of a project in the order they were imported.

    \N{FORM FEED}
    :param project_id: Identifier of the project.
    :type project_id: ProjectId
    :param actor: The signed-in account.
    :type actor: Actor
    :param params: Page number and size from the query.
    :type params: Params
    :param sources: Source service of the request.
    :type sources: SourceService
    :returns: One page of the project's sources.
    :rtype: Page[SourceSchema]
    """
    pager = Pager[Source, SourceSchema](params, SourceSchema.model_validate)
    return pager.page(await sources.list(actor, project_id, pager.request))


@router.get('/{project_id}/sources/{source_id}')
async def get_source(
    project_id: Annotated[ProjectId, Path(description=PROJECT_ID_DESCRIPTION)],
    source_id: Annotated[SourceId, Path(description=SOURCE_ID_DESCRIPTION)],
    actor: ActorDep,
    sources: FromDishka[SourceService],
) -> SourceSchema:
    """Return one source of a project.

    \N{FORM FEED}
    :param project_id: Identifier of the project.
    :type project_id: ProjectId
    :param source_id: Identifier of the source.
    :type source_id: SourceId
    :param actor: The signed-in account.
    :type actor: Actor
    :param sources: Source service of the request.
    :type sources: SourceService
    :returns: The source.
    :rtype: SourceSchema
    """
    return SourceSchema.model_validate(await sources.get(actor, project_id, source_id))


@router.delete('/{project_id}/sources/{source_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(
    project_id: Annotated[ProjectId, Path(description=PROJECT_ID_DESCRIPTION)],
    source_id: Annotated[SourceId, Path(description=SOURCE_ID_DESCRIPTION)],
    actor: ActorDep,
    sources: FromDishka[SourceService],
) -> None:
    """Delete a source with its files and its scans, leaving the pages of the book with their own images.

    The pages made from the source lose only the reference to their scan. The project answers 409 while it imports.

    \N{FORM FEED}
    :param project_id: Identifier of the project.
    :type project_id: ProjectId
    :param source_id: Identifier of the source.
    :type source_id: SourceId
    :param actor: The signed-in account.
    :type actor: Actor
    :param sources: Source service of the request.
    :type sources: SourceService
    """
    await sources.delete(actor, project_id, source_id)


@router.get('/{project_id}/scans')
async def list_scans(
    project_id: Annotated[ProjectId, Path(description=PROJECT_ID_DESCRIPTION)],
    actor: ActorDep,
    request: Request,
    query: Annotated[ScanQuery, Depends()],
    sources: FromDishka[SourceService],
) -> Page[ScanSchema]:
    """List the scans of a project source by source, or of one source, with the paths of their images once cut.

    \N{FORM FEED}
    :param project_id: Identifier of the project.
    :type project_id: ProjectId
    :param actor: The signed-in account.
    :type actor: Actor
    :param request: The request, whose application knows the route that serves the images.
    :type request: Request
    :param query: Page number and size, and the source to list the scans of, if any.
    :type query: ScanQuery
    :param sources: Source service of the request.
    :type sources: SourceService
    :returns: One page of scans.
    :rtype: Page[ScanSchema]
    """
    pager = Pager[Scan, ScanSchema](query, partial(ScanSchema.from_scan, request=request))
    return pager.page(await sources.scans(actor, project_id, pager.request, source_id=query.source_id))
