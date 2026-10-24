"""Fixtures giving the port contract tests every adapter of the port, built the way the application builds it.

The persistence port has one adapter, so its contract tests run on the root ``fx_database``, ``fx_uow`` and
``fx_databases``.
"""

from typing import TYPE_CHECKING, NamedTuple

import pytest
from dishka import make_async_container

from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
from bookreviver.app.providers.storage import STORAGE_PROVIDERS
from bookreviver.app.settings import Settings
from bookreviver.ports.storage import AssetStore, BookArchive, SourceStore
from tests.helpers.builders import make_record
from tests.helpers.job_boards import BoardAdapter
from tests.helpers.seeding import commit_account, store_book
from tests.helpers.step_executors import StepExecutorAdapter
from tests.helpers.tracers import TracerAdapter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from dishka import AsyncContainer

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import BookRecord
    from bookreviver.domain.ids import AccountId
    from bookreviver.ports.ordering import OrderKeys
    from bookreviver.ports.persistence import BookDatabases
    from tests.helpers.database import BookDatabasesFactory, UnitOfWorkFactory

type OwnerFactory = Callable[[], Awaitable[AccountId]]

# Every adapter of the OrderKeys port by name; each computes keys in memory, so none needs a provider
ORDER_KEYS_ADAPTERS: dict[str, type[OrderKeys]] = {'fractional-indexing': FractionalOrderKeys}


@pytest.fixture
def fx_new_owner(fx_database: SqlDatabase) -> OwnerFactory:
    """Return a function creating an account that may own books.

    The database checks the owner of a book against the account table of fastapi-users, so an account is a
    committed row.

    :param fx_database: Database of the test.
    :type fx_database: SqlDatabase
    :returns: Function returning the identifier of a new account.
    :rtype: OwnerFactory
    """

    async def new_owner() -> AccountId:
        """Create an account in the database of the test.

        :returns: Identifier of the new account.
        :rtype: AccountId
        """
        return await commit_account(fx_database)

    return new_owner


@pytest.fixture(params=list(ORDER_KEYS_ADAPTERS), ids=str)
def fx_order_keys(request: pytest.FixtureRequest) -> OrderKeys:
    """Return each adapter of the OrderKeys port in turn.

    :param request: Request of the parametrized fixture, whose ``param`` names the adapter.
    :type request: pytest.FixtureRequest
    :returns: The adapter under test.
    :rtype: OrderKeys
    """
    return ORDER_KEYS_ADAPTERS[request.param]()


@pytest.fixture(params=list(BoardAdapter), ids=str)
def fx_board_adapter(request: pytest.FixtureRequest) -> BoardAdapter:
    """Return each adapter of the JobBoard port in turn.

    :param request: Request of the parametrized fixture, whose ``param`` is the adapter.
    :type request: pytest.FixtureRequest
    :returns: The adapter under test, set up by ``open_board_under_test``.
    :rtype: BoardAdapter
    """
    return BoardAdapter(request.param)


@pytest.fixture(params=list(StepExecutorAdapter), ids=str)
def fx_step_executor_adapter(request: pytest.FixtureRequest) -> StepExecutorAdapter:
    """Return each adapter of the StepExecutor port in turn.

    :param request: Request of the parametrized fixture, whose ``param`` is the adapter.
    :type request: pytest.FixtureRequest
    :returns: The adapter under test, set up by ``open_executor_under_test``.
    :rtype: StepExecutorAdapter
    """
    return StepExecutorAdapter(request.param)


@pytest.fixture(params=list(TracerAdapter), ids=str)
def fx_tracer_adapter(request: pytest.FixtureRequest) -> TracerAdapter:
    """Return each adapter of the Tracer port in turn.

    :param request: Request of the parametrized fixture, whose ``param`` is the adapter.
    :type request: pytest.FixtureRequest
    :returns: The adapter under test, set up by ``open_tracer_under_test``.
    :rtype: TracerAdapter
    """
    return TracerAdapter(request.param)


@pytest.fixture(params=[adapter for adapter in TracerAdapter if adapter.keeps_spans], ids=str)
def fx_recording_adapter(request: pytest.FixtureRequest) -> TracerAdapter:
    """Return each adapter of the Tracer port that keeps the spans it is given, in turn.

    :param request: Request of the parametrized fixture, whose ``param`` is the adapter.
    :type request: pytest.FixtureRequest
    :returns: The adapter under test, set up by ``open_tracer_under_test``.
    :rtype: TracerAdapter
    """
    return TracerAdapter(request.param)


@pytest.fixture(params=list(STORAGE_PROVIDERS), ids=str)
async def fx_storage(request: pytest.FixtureRequest, fx_settings: Settings) -> AsyncIterator[AsyncContainer]:
    """Yield a container holding the provider of one storage backend, rooted in the test's data directory.

    :param request: Request of the parametrized fixture, whose ``param`` is the storage backend.
    :type request: pytest.FixtureRequest
    :param fx_settings: Settings with a fresh data directory of the test.
    :type fx_settings: Settings
    :returns: Iterator yielding the container and closing it afterwards.
    :rtype: AsyncIterator[AsyncContainer]
    """
    container = make_async_container(STORAGE_PROVIDERS[request.param](), context={Settings: fx_settings})
    yield container
    await container.close()


@pytest.fixture
async def fx_source_store(fx_storage: AsyncContainer) -> SourceStore:
    """Return the source store of the storage backend under test.

    :param fx_storage: Container holding the provider of one storage backend.
    :type fx_storage: AsyncContainer
    :returns: The source store the backend's provider builds.
    :rtype: SourceStore
    """
    return await fx_storage.get(SourceStore)


@pytest.fixture
async def fx_book_archive(fx_storage: AsyncContainer) -> BookArchive:
    """Return the book archive of the storage backend under test, over the same root as its stores.

    :param fx_storage: Container holding the provider of one storage backend.
    :type fx_storage: AsyncContainer
    :returns: The book archive the backend's provider builds.
    :rtype: BookArchive
    """
    return await fx_storage.get(BookArchive)


@pytest.fixture
async def fx_asset_store(fx_storage: AsyncContainer) -> AssetStore:
    """Return the asset store of the storage backend under test.

    :param fx_storage: Container holding the provider of one storage backend.
    :type fx_storage: AsyncContainer
    :returns: The asset store the backend's provider builds.
    :rtype: AssetStore
    """
    return await fx_storage.get(AssetStore)


class Book(NamedTuple):
    """A stored book and the account that owns it.

    :ivar book_record: The record of the book.
    :ivar owner_id: The account that owns the book.
    """

    book_record: BookRecord
    owner_id: AccountId


async def _store_book(uow_factory: UnitOfWorkFactory, new_owner: OwnerFactory, *, databases: BookDatabases) -> Book:
    """Store a book of a new account.

    :param uow_factory: Function opening a new unit of work over the catalogue and the books of the test.
    :type uow_factory: UnitOfWorkFactory
    :param new_owner: Function creating an account the catalogue accepts as an owner.
    :type new_owner: OwnerFactory
    :param databases: Opens the databases of the books of the test.
    :type databases: BookDatabases
    :returns: The stored book.
    :rtype: Book
    """
    book_record, owner_id = make_record(), await new_owner()
    await store_book(uow_factory(), book_record, owner=owner_id, databases=databases)
    return Book(book_record=book_record, owner_id=owner_id)


@pytest.fixture
async def fx_book(fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory) -> Book:
    """Store a book that the changes of the tests change.

    :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
    :type fx_uow: UnitOfWorkFactory
    :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
    :type fx_new_owner: OwnerFactory
    :param fx_databases: Function opening a new opener of the databases of the books of the test.
    :type fx_databases: BookDatabasesFactory
    :returns: The stored book.
    :rtype: Book
    """
    return await _store_book(fx_uow, fx_new_owner, databases=fx_databases())


@pytest.fixture
async def fx_other_book(
    fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
) -> Book:
    """Store a second book, which a change of the first book must not wait for.

    :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
    :type fx_uow: UnitOfWorkFactory
    :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
    :type fx_new_owner: OwnerFactory
    :param fx_databases: Function opening a new opener of the databases of the books of the test.
    :type fx_databases: BookDatabasesFactory
    :returns: The stored book.
    :rtype: Book
    """
    return await _store_book(fx_uow, fx_new_owner, databases=fx_databases())
