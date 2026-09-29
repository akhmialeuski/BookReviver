"""Storing what earlier requests would have stored, so a test starts from a known committed state."""

from typing import TYPE_CHECKING

from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork

if TYPE_CHECKING:
    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.domain.entities import Page, Project


async def commit_project(database: InMemoryDatabase, project: Project, *pages: Page) -> None:
    """Commit a project and its pages in one unit of work.

    :param database: In-memory database to commit into.
    :type database: InMemoryDatabase
    :param project: Project to store.
    :type project: Project
    :param pages: Pages of the project to store with it.
    :type pages: Page
    """
    uow = InMemoryUnitOfWork(database)
    await uow.projects.add(project)
    await uow.pages.replace_for_project(project.id, pages)
    await uow.commit()
