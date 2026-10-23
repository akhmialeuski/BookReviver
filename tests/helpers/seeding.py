"""Storing what earlier requests would have stored, so a test starts from a known committed state."""

from typing import TYPE_CHECKING, TypedDict, Unpack

from bookreviver.adapters.persistence.sqlalchemy.accounts import AccountTable
from bookreviver.domain.entities import LibraryEntry
from bookreviver.domain.enums import BlockKind
from bookreviver.ports.persistence import DEFAULT_CHANGE_WAIT_SECONDS
from tests.helpers.builders import new_account_id

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import Page, PageVersion, Project, Scan, Source
    from bookreviver.domain.ids import AccountId
    from bookreviver.ports.persistence import BookRepositories, UnitOfWork

# Domain of the addresses of seeded accounts, reserved for examples by RFC 2606
ACCOUNT_DOMAIN: str = 'example.org'


class BookContent(TypedDict, total=False):
    """What a seeded book holds besides its project and its pages, each part left out when a test needs none.

    :ivar sources: Sources of the project.
    :ivar scans: Scans of those sources.
    :ivar versions: Versions of the pages.
    """

    sources: Sequence[Source]
    scans: Sequence[Scan]
    versions: Sequence[PageVersion]


async def commit_account(
    database: SqlDatabase, *, account_id: AccountId | None = None, hashed_password: str = ''
) -> AccountId:
    """Commit a verified account, which the library entry of a book in the catalogue refers to.

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
    """Commit the account unless the catalogue stores it already, as the library entry of a book needs it.

    The catalogue of a test stores the signed-in actor's account from the start, so a book of the actor needs
    nothing more, and a book of any other account needs the account committed first.

    :param database: Catalogue to commit into.
    :type database: SqlDatabase
    :param account_id: Identifier of the account that owns a book.
    :type account_id: AccountId
    """
    if await database.read(lambda session: session.get(AccountTable, account_id)) is None:
        await commit_account(database, account_id=account_id)


async def store_project(
    uow: UnitOfWork, project: Project, *pages: Page, owner: AccountId, **content: Unpack[BookContent]
) -> None:
    """Store a book with its project, then its sources, their scans, its pages and their versions, and list it.

    The store of the book is made first, then the content of the book is stored by ``change_book``, and the library entry
    of the owner is added last, as the creation of a book does. The entry names an account, so the catalogue must store
    the owner.

    :param uow: Unit of work to store through, over a catalogue that stores the account owning the book.
    :type uow: UnitOfWork
    :param project: Project to store.
    :type project: Project
    :param pages: Pages of the project, which may show the given scans.
    :type pages: Page
    :param owner: Account owning the book.
    :type owner: AccountId
    :param content: The sources of the project, their scans and the versions of the pages, as far as the book has them.
    :type content: Unpack[BookContent]
    """

    def store_content(repos: BookRepositories, _project: Project) -> None:
        """Store the sources, scans, pages and versions of the book in the order their keys need.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :param _project: Project of the book, which the work does not need.
        :type _project: Project
        """
        repos.sources.add_many(content.get('sources', ()))
        repos.scans.add_many(content.get('scans', ()))
        repos.pages.add_many(pages)
        repos.page_versions.add_many(content.get('versions', ()))

    await uow.create_book(project)
    await uow.change_book(project.id, store_content)
    entry = LibraryEntry(project_id=project.id, owner_id=owner, added_at=project.created_at)
    await uow.change(lambda repos: repos.library.add(entry))


async def commit_project(
    database: SqlDatabase,
    uow: UnitOfWork,
    project: Project,
    *pages: Page,
    owner: AccountId,
    **content: Unpack[BookContent],
) -> None:
    """Store a book as ``store_project`` does, after committing its owner account when the catalogue lacks it.

    :param database: Catalogue to commit the owner account into.
    :type database: SqlDatabase
    :param uow: Unit of work to store the book through, over the same catalogue.
    :type uow: UnitOfWork
    :param project: Project to store.
    :type project: Project
    :param pages: Pages of the project, which may show the given scans.
    :type pages: Page
    :param owner: Account owning the book, which is committed first unless the catalogue stores it.
    :type owner: AccountId
    :param content: The sources of the project, their scans and the versions of the pages, as far as the book has them.
    :type content: Unpack[BookContent]
    """
    await ensure_account(database, owner)
    await store_project(uow, project, *pages, owner=owner, **content)
