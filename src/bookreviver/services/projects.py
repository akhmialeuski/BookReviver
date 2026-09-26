"""Use cases of projects: listing, creating, describing and deleting books."""

from typing import TYPE_CHECKING
from uuid import uuid4

from attrs import evolve

from bookreviver.domain.entities import Project, ProjectOverview
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.ids import ProjectId
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import SliceRequest

if TYPE_CHECKING:
    from bookreviver.domain.changes import BookDetailsChanges
    from bookreviver.domain.entities import Actor
    from bookreviver.domain.values import BookDetails, Slice
    from bookreviver.ports.persistence import ProjectRepository, UnitOfWork
    from bookreviver.ports.runtime import Clock
    from bookreviver.ports.storage import AssetStore, SourceStore

# The smallest page window; only the total of the result is read
PAGE_COUNT_REQUEST: SliceRequest = SliceRequest(limit=1)


async def owned_project(projects: ProjectRepository, actor: Actor, project_id: ProjectId) -> Project:
    """Return the actor's project.

    :raises NotFoundError: If the project does not exist or belongs to another account, so that identifiers of other
        accounts' projects cannot be probed.
    """
    project = await projects.get(project_id)
    if not project.is_owned_by(actor):
        raise NotFoundError(project_id)
    return project


class ProjectService:
    """Projects of the acting account, with their description and their files."""

    def __init__(self, uow: UnitOfWork, clock: Clock, sources: SourceStore, assets: AssetStore) -> None:
        self._uow = uow
        self._clock = clock
        self._sources = sources
        self._assets = assets

    async def list(self, actor: Actor, request: SliceRequest) -> Slice[ProjectOverview]:
        """Return a slice of the actor's projects, most recently updated first."""
        return await self._uow.projects.list_for_owner(actor.account_id, request)

    async def create(self, actor: Actor, details: BookDetails) -> ProjectOverview:
        """Create an empty project owned by the actor."""
        moment = self._clock.now()
        project = Project(
            id=ProjectId(uuid4()), owner_id=actor.account_id, details=details, created_at=moment, updated_at=moment
        )
        stored = await self._uow.projects.add(project)
        await self._uow.commit()
        return ProjectOverview(project=stored, page_count=0)

    async def get(self, actor: Actor, project_id: ProjectId) -> ProjectOverview:
        """Return one of the actor's projects.

        :raises NotFoundError: If the actor has no such project.
        """
        return await self._overview(await owned_project(self._uow.projects, actor, project_id))

    async def update_details(self, actor: Actor, project_id: ProjectId, changes: BookDetailsChanges) -> ProjectOverview:
        """Change some fields of the project's description.

        :raises NotFoundError: If the actor has no such project.
        """
        project = await owned_project(self._uow.projects, actor, project_id)
        changed = evolve(project, details=changes.apply_to(project.details), updated_at=self._clock.now())
        stored = await self._uow.projects.update(changed)
        await self._uow.commit()
        return await self._overview(stored)

    async def delete(self, actor: Actor, project_id: ProjectId) -> None:
        """Delete the project with its pages and jobs, then its source and derived files.

        :raises NotFoundError: If the actor has no such project.
        """
        await owned_project(self._uow.projects, actor, project_id)
        await self._uow.projects.delete(project_id)
        await self._uow.commit()
        # Files go only once the row is gone, so a failure leaves orphan files rather than a project without files
        await self._sources.delete_project(project_id)
        await self._assets.delete_prefix(ProjectKeys(project_id).prefix)

    async def _overview(self, project: Project) -> ProjectOverview:
        """Pair a project with the number of its pages."""
        pages = await self._uow.pages.list_for_project(project.id, PAGE_COUNT_REQUEST)
        return ProjectOverview(project=project, page_count=pages.total)
