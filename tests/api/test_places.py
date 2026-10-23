"""Tests for the place endpoints, on the SQLite file of the test."""

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from delayed_assert import assert_expectations, expect
from fastapi import status

from bookreviver.api.schemas.places import BookPlaceSchema
from bookreviver.api.schemas.types import CANVAS_CENTRE_MAX, CANVAS_ZOOM_MAX
from bookreviver.domain.enums import CompareMode, PageFilter, PlaceMode, Stage, ViewMode
from tests.helpers.builders import make_record, new_account_id
from tests.helpers.seeding import commit_record, store_book

if TYPE_CHECKING:
    import httpx

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import Actor, BookRecord
    from tests.helpers.database import UnitOfWorkFactory

pytestmark = pytest.mark.anyio

BOOKS_PATH: str = '/api/v1/books'
CANVAS_FIELD: str = 'canvas'
ZOOM_FIELD: str = 'zoom'
STAGE_FIELD: str = 'stage'
MODE_FIELD: str = 'mode'
VIEW_FIELD: str = 'view'
PAGE_FIELD: str = 'page_id'


def _canvas(zoom: float, centre_x: float = 0.0, centre_y: float = 0.0) -> dict[str, float]:
    """Build the canvas position of a body.

    :param zoom: Multiple of the fitted view.
    :type zoom: float
    :param centre_x: Horizontal coordinate of the centre in page heights.
    :type centre_x: float
    :param centre_y: Vertical coordinate of the centre in page heights.
    :type centre_y: float
    :returns: The JSON object of the position.
    :rtype: dict[str, float]
    """
    return {ZOOM_FIELD: zoom, 'centre_x': centre_x, 'centre_y': centre_y}


def _path(book_record: BookRecord) -> str:
    """Return the path of the place of a book.

    :param book_record: The book.
    :type book_record: BookRecord
    :returns: The path of its place.
    :rtype: str
    """
    return f'{BOOKS_PATH}/{book_record.id}/place'


def _body() -> dict[str, Any]:
    """Build the body of a place of a reader who zoomed into a page of a stage in the spread layout.

    :returns: The JSON body of a request.
    :rtype: dict[str, Any]
    """
    return {
        MODE_FIELD: PlaceMode.WORKSPACE,
        STAGE_FIELD: Stage.GEOMETRY,
        PAGE_FIELD: str(uuid4()),
        VIEW_FIELD: ViewMode.SPREAD,
        'compare': CompareMode.SWIPE,
        'filter': PageFilter.CHECK,
        CANVAS_FIELD: _canvas(2.5, 0.4, 0.6),
        'strip_page_id': str(uuid4()),
    }


@pytest.fixture
async def fx_book(fx_uow: UnitOfWorkFactory, fx_actor: Actor) -> BookRecord:
    """Commit a book of the signed-in account.

    :param fx_uow: Function opening a new unit of work over the database of the test.
    :type fx_uow: UnitOfWorkFactory
    :param fx_actor: The signed-in account.
    :type fx_actor: Actor
    :returns: The stored book.
    :rtype: BookRecord
    """
    book_record = make_record()
    await store_book(fx_uow(), book_record, owner=fx_actor.account_id)
    return book_record


class TestGetPlace:
    """Tests for GET /books/{id}/place."""

    async def test_a_book_not_worked_on_answers_no_content(
        self, fx_client: httpx.AsyncClient, fx_book: BookRecord
    ) -> None:
        """Verify a book the account has not worked on answers 204 with no body.

        :param fx_client: HTTP client of the application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: A book of the signed-in account.
        :type fx_book: BookRecord
        """
        response = await fx_client.get(_path(fx_book))
        expect(response.status_code == status.HTTP_204_NO_CONTENT)
        expect(response.content == b'')
        assert_expectations()

    async def test_the_place_that_was_put_is_returned(self, fx_client: httpx.AsyncClient, fx_book: BookRecord) -> None:
        """Verify a place written by PUT is read back as written.

        :param fx_client: HTTP client of the application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: A book of the signed-in account.
        :type fx_book: BookRecord
        """
        body = _body()
        await fx_client.put(_path(fx_book), json=body)
        response = await fx_client.get(_path(fx_book))
        place = BookPlaceSchema.model_validate(response.json())
        expect(response.status_code == status.HTTP_200_OK)
        expect(place.stage == Stage.GEOMETRY)
        expect(place.view == ViewMode.SPREAD)
        expect(place.canvas is not None and place.canvas.zoom == body[CANVAS_FIELD][ZOOM_FIELD])
        expect(str(place.page_id) == body[PAGE_FIELD])
        assert_expectations()

    async def test_the_book_of_another_account_is_not_found(
        self, fx_client: httpx.AsyncClient, fx_database: SqlDatabase, fx_uow: UnitOfWorkFactory
    ) -> None:
        """Verify the place of a book of another account answers 404, as a missing book does.

        :param fx_client: HTTP client of the application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: Database of the test, which the application runs on.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        """
        strangers = make_record()
        await commit_record(fx_database, fx_uow(), strangers, owner=new_account_id())
        response = await fx_client.get(_path(strangers))
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestPutPlace:
    """Tests for PUT /books/{id}/place."""

    async def test_put_replaces_the_place(self, fx_client: httpx.AsyncClient, fx_book: BookRecord) -> None:
        """Verify a second PUT replaces the first, and a field left out takes its default.

        :param fx_client: HTTP client of the application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: A book of the signed-in account.
        :type fx_book: BookRecord
        """
        await fx_client.put(_path(fx_book), json=_body())
        response = await fx_client.put(_path(fx_book), json={MODE_FIELD: PlaceMode.READING, STAGE_FIELD: Stage.LAYOUT})
        place = BookPlaceSchema.model_validate(response.json())
        expect(response.status_code == status.HTTP_200_OK)
        expect((place.mode, place.stage) == (PlaceMode.READING, Stage.LAYOUT))
        expect((place.view, place.compare, place.filter) == (ViewMode.PAGE, CompareMode.OFF, PageFilter.ALL))
        expect(place.canvas is None and place.page_id is None)
        assert_expectations()

    async def test_the_book_of_another_account_is_not_found(
        self, fx_client: httpx.AsyncClient, fx_database: SqlDatabase, fx_uow: UnitOfWorkFactory
    ) -> None:
        """Verify a place cannot be written into a book of another account.

        :param fx_client: HTTP client of the application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: Database of the test, which the application runs on.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        """
        strangers = make_record()
        await commit_record(fx_database, fx_uow(), strangers, owner=new_account_id())
        response = await fx_client.put(_path(strangers), json=_body())
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize(
        'change',
        [
            {STAGE_FIELD: 'binding'},
            {VIEW_FIELD: 'carousel'},
            {PAGE_FIELD: 'not-an-identifier'},
            {'unknown_field': 1},
            {CANVAS_FIELD: _canvas(0)},
            {CANVAS_FIELD: _canvas(CANVAS_ZOOM_MAX * 2)},
            {CANVAS_FIELD: _canvas(1, centre_x=CANVAS_CENTRE_MAX * 2)},
            {CANVAS_FIELD: {ZOOM_FIELD: 1}},
        ],
        ids=['stage', 'view', 'page', 'unknown-field', 'zoom-zero', 'zoom-huge', 'centre-far', 'centre-missing'],
    )
    async def test_an_invalid_body_is_refused(
        self, fx_client: httpx.AsyncClient, fx_book: BookRecord, change: dict[str, Any]
    ) -> None:
        """Verify a body that breaks a constraint is a 422 and stores nothing.

        :param fx_client: HTTP client of the application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: A book of the signed-in account.
        :type fx_book: BookRecord
        :param change: Fields of a valid body to replace with an invalid value.
        :type change: dict[str, Any]
        """
        response = await fx_client.put(_path(fx_book), json={**_body(), **change})
        expect(response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT)
        expect((await fx_client.get(_path(fx_book))).status_code == status.HTTP_204_NO_CONTENT)
        assert_expectations()
