"""Storing what earlier requests would have stored, so a test starts from a known committed state."""

from typing import TYPE_CHECKING

from bookreviver.adapters.persistence.sqlalchemy.accounts import AccountTable
from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from bookreviver.domain.enums import BlockKind
from bookreviver.ports.persistence import DEFAULT_CHANGE_WAIT_SECONDS
from tests.helpers.builders import new_account_id

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import Page, PageVersion, Project, Scan, Source
    from bookreviver.domain.ids import AccountId
    from bookreviver.ports.persistence import Repositories, UnitOfWork

# Domain of the addresses of seeded accounts, reserved for examples by RFC 2606
ACCOUNT_DOMAIN: str = 'example.org'


async def commit_account(
    database: SqlDatabase, *, account_id: AccountId | None = None, hashed_password: str = ''
) -> AccountId:
    """Commit a verified account, which the owner key of a project in the SQL database refers to.

    :param database: SQL database to commit into.
    :type database: SqlDatabase
    :param account_id: Identifier the account gets, for a test whose signed-in actor exists before the database does;
                       a new one when omitted.
    :type account_id: AccountId | None
    :param hashed_password: Password hash the account stores, for a test that signs in; empty, which matches no
                            password, when omitted.
    :type hashed_password: str
    :returns: Identifier of the account.
    :rtype: AccountId
    """
    if account_id is None:
        account_id = new_account_id()
    account = AccountTable(
        id=account_id,
        email=f'{account_id}@{ACCOUNT_DOMAIN}',
        hashed_password=hashed_password,
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    await database.write(
        lambda session: session.add(account),
        kind=BlockKind.OTHER,
        book=None,
        wait_seconds=DEFAULT_CHANGE_WAIT_SECONDS,
    )
    return account_id


async def ensure_account(database: SqlDatabase, account_id: AccountId) -> None:
    """Commit the account unless the database stores it already, as the owner key of a project needs it.

    The database of a test stores the signed-in actor's account from the start, so a project of the actor needs
    nothing more, and a project of any other account needs the account committed first.

    :param database: SQL database to commit into.
    :type database: SqlDatabase
    :param account_id: Identifier of the account that owns a project.
    :type account_id: AccountId
    """
    if await database.read(lambda session: session.get(AccountTable, account_id)) is None:
        await commit_account(database, account_id=account_id)


async def store_project(
    uow: UnitOfWork,
    project: Project,
    *pages: Page,
    sources: Sequence[Source] = (),
    scans: Sequence[Scan] = (),
    versions: Sequence[PageVersion] = (),
) -> None:
    """Store a project, then its sources, their scans, its pages and their versions, each in the block that owns it.

    The project is a change outside the content of a book, so it is stored by ``change``. The rest is the content of
    the book, so it is stored by ``change_book`` of the project, which cannot open before the project exists.

    :param uow: Unit of work to store through, over a database that stores the account owning the project.
    :type uow: UnitOfWork
    :param project: Project to store.
    :type project: Project
    :param pages: Pages of the project, which may show the given scans.
    :type pages: Page
    :param sources: Sources of the project.
    :type sources: Sequence[Source]
    :param scans: Scans of those sources.
    :type scans: Sequence[Scan]
    :param versions: Versions of those pages.
    :type versions: Sequence[PageVersion]
    """

    def store_content(repos: Repositories, _project: Project) -> None:
        """Store the sources, scans, pages and versions of the book in the order their keys need.

        :param repos: Repositories of the transaction.
        :type repos: Repositories
        :param _project: Project of the book, which the work does not need.
        :type _project: Project
        """
        repos.sources.add_many(sources)
        repos.scans.add_many(scans)
        repos.pages.add_many(pages)
        repos.page_versions.add_many(versions)

    await uow.change(lambda repos: repos.projects.add(project))
    await uow.change_book(project.id, store_content)


async def commit_project(
    database: SqlDatabase,
    project: Project,
    *pages: Page,
    sources: Sequence[Source] = (),
    scans: Sequence[Scan] = (),
    versions: Sequence[PageVersion] = (),
) -> None:
    """Commit a project with its sources, their scans, its pages and their versions, and its owner when it is new.

    :param database: SQL database to commit into.
    :type database: SqlDatabase
    :param project: Project to store, whose owner account is committed first unless the database stores it.
    :type project: Project
    :param pages: Pages of the project, which may show the given scans.
    :type pages: Page
    :param sources: Sources of the project.
    :type sources: Sequence[Source]
    :param scans: Scans of those sources.
    :type scans: Sequence[Scan]
    :param versions: Versions of those pages.
    :type versions: Sequence[PageVersion]
    """
    await ensure_account(database, project.owner_id)
    await store_project(
        SqlAlchemyUnitOfWork(database), project, *pages, sources=sources, scans=scans, versions=versions
    )
