"""Storing what earlier requests would have stored, so a test starts from a known committed state."""

from typing import TYPE_CHECKING

from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.adapters.persistence.sqlalchemy.accounts import AccountTable
from tests.helpers.builders import new_account_id

if TYPE_CHECKING:
    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import Page, Project
    from bookreviver.domain.ids import AccountId

# Domain of the addresses of seeded accounts, reserved for examples by RFC 2606
ACCOUNT_DOMAIN: str = 'example.org'


async def commit_account(database: SqlDatabase) -> AccountId:
    """Commit a verified account, which the owner key of a project in the SQL database refers to.

    :param database: SQL database to commit into.
    :type database: SqlDatabase
    :returns: Identifier of the new account.
    :rtype: AccountId
    """
    account_id = new_account_id()
    async with database.sessions() as session:
        session.add(
            AccountTable(
                id=account_id,
                email=f'{account_id}@{ACCOUNT_DOMAIN}',
                hashed_password='',
                is_active=True,
                is_superuser=False,
                is_verified=True,
            )
        )
        await session.commit()
    return account_id


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
