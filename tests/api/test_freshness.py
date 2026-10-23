"""Tests for the conditional reads of a book: the tag of a read, the 304 of a read that holds, and the reads left out."""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect
from fastapi import status

from bookreviver.api.freshness import CACHE_CONTROL_HEADER, ETAG_HEADER, IF_NONE_MATCH_HEADER, REVALIDATE
from tests.helpers.builders import make_page, make_record, new_account_id
from tests.helpers.seeding import commit_record, store_book

if TYPE_CHECKING:
    import httpx

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import Actor, BookRecord, Page
    from tests.helpers.database import UnitOfWorkFactory

pytestmark = pytest.mark.anyio

BOOKS_PATH: str = '/api/v1/books'
GROUP_FIELD: str = 'group_label'
GROUP: str = 'Engravings'


def _pages_path(book_record: BookRecord) -> str:
    """Return the path of the pages of a book.

    :param book_record: The book.
    :type book_record: BookRecord
    :returns: The path of the read of its pages.
    :rtype: str
    """
    return f'{BOOKS_PATH}/{book_record.id}/pages'


def _page_path(book_record: BookRecord, page: Page) -> str:
    """Return the path of one page of a book.

    :param book_record: The book.
    :type book_record: BookRecord
    :param page: The page.
    :type page: Page
    :returns: The path of the page.
    :rtype: str
    """
    return f'{_pages_path(book_record)}/{page.id}'


class TestConditionalRead:
    """Tests for a read of a book, which is tagged and answered 304 while it still holds."""

    async def test_a_read_that_still_holds_is_answered_304_and_a_change_gives_a_new_tag(
        self, fx_client: httpx.AsyncClient, fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify the read carries a weak tag, its repetition gets 304 with no body, and a change of a page a new tag.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        """
        book_record, page = make_record(), make_page()
        await store_book(fx_uow(), book_record, page, owner=fx_actor.account_id)

        first = await fx_client.get(_pages_path(book_record))
        tag = first.headers[ETAG_HEADER]
        repeated = await fx_client.get(_pages_path(book_record), headers={IF_NONE_MATCH_HEADER: tag})
        await fx_client.patch(_page_path(book_record, page), json={GROUP_FIELD: GROUP})
        changed = await fx_client.get(_pages_path(book_record), headers={IF_NONE_MATCH_HEADER: tag})

        expect(first.status_code == status.HTTP_200_OK and tag.startswith('W/"'))
        expect(first.headers[CACHE_CONTROL_HEADER] == REVALIDATE)
        expect((repeated.status_code, repeated.content) == (status.HTTP_304_NOT_MODIFIED, b''))
        expect(repeated.headers[ETAG_HEADER] == tag)
        expect(changed.status_code == status.HTTP_200_OK)
        expect(changed.headers[ETAG_HEADER] != tag)
        assert_expectations()

    async def test_two_reads_of_one_book_have_two_tags(
        self, fx_client: httpx.AsyncClient, fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify the tag of the pages does not answer the read of one page, which shows something else.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        """
        book_record, page = make_record(), make_page()
        await store_book(fx_uow(), book_record, page, owner=fx_actor.account_id)
        tag = (await fx_client.get(_pages_path(book_record))).headers[ETAG_HEADER]

        response = await fx_client.get(_page_path(book_record, page), headers={IF_NONE_MATCH_HEADER: tag})

        assert response.status_code == status.HTTP_200_OK

    async def test_another_accounts_book_is_not_found_whatever_tag_is_sent(
        self, fx_client: httpx.AsyncClient, fx_database: SqlDatabase, fx_uow: UnitOfWorkFactory
    ) -> None:
        """Verify a read of another account's book answers 404 even with a tag that matches any.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: Database of the test, which the application runs on.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        """
        book_record = make_record()
        await commit_record(fx_database, fx_uow(), book_record, owner=new_account_id())

        response = await fx_client.get(_pages_path(book_record), headers={IF_NONE_MATCH_HEADER: '*'})

        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_the_place_of_the_reader_is_not_tagged(
        self, fx_client: httpx.AsyncClient, fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify the place, which changes without raising the version, is read every time.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        """
        book_record = make_record()
        await store_book(fx_uow(), book_record, owner=fx_actor.account_id)

        response = await fx_client.get(f'{BOOKS_PATH}/{book_record.id}/place')

        assert ETAG_HEADER not in response.headers
