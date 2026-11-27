"""Tests for the addresses of the images of a book, which a list of many pages builds thousands of."""

from typing import TYPE_CHECKING

from delayed_assert import assert_expectations, expect
from fastapi import FastAPI
from starlette.requests import Request

from bookreviver.api.route_names import RouteName
from bookreviver.api.routers.iiif import router
from bookreviver.api.schemas.images import iiif_path
from tests.harness.builders.domain import new_book_id

if TYPE_CHECKING:
    import pytest

KEYS: tuple[str, ...] = ('pages/a/v1/thumbnail.jpg', 'pages/a/v1/tiles/info.json', 'scans/b c/full.png')
HTTP_SCOPE: str = 'http'


def _request(app: FastAPI) -> Request:
    """Make a request of the application, which the addresses are built with.

    :param app: The application.
    :type app: FastAPI
    :returns: The request.
    :rtype: Request
    """
    return Request({'type': HTTP_SCOPE, 'app': app})


class TestIiifPath:
    """Tests for the path of a file of a book on the IIIF route."""

    def test_the_path_is_the_one_the_route_gives_for_the_key(self) -> None:
        """Verify every key, nested or with a space, gives the path the route builds for it, for two books."""
        app = FastAPI()
        app.include_router(router)
        request = _request(app)
        for book_id in (new_book_id(), new_book_id()):
            for key in KEYS:
                expected = str(app.url_path_for(RouteName.IIIF_FILE, book_id=str(book_id), key=key))
                expect(iiif_path(request, book_id, key) == expected, f'{key}: {iiif_path(request, book_id, key)}')
        assert_expectations()

    def test_the_routes_are_searched_once_for_a_book(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify many files of one book look the route up once, since a search walks every route of the application.

        :param monkeypatch: Counts the searches of the route by its name.
        :type monkeypatch: pytest.MonkeyPatch
        """
        app = FastAPI()
        app.include_router(router)
        request = _request(app)
        searches: list[str] = []
        search = app.url_path_for

        def counted(name: str, /, **params: str) -> str:
            """Search the route by its name and count the search.

            :param name: Name of the route.
            :type name: str
            :param params: Parameters of the path.
            :type params: str
            :returns: The path.
            :rtype: str
            """
            searches.append(name)
            return search(name, **params)

        monkeypatch.setattr(app, 'url_path_for', counted)
        book_id = new_book_id()
        for key in KEYS * 10:
            iiif_path(request, book_id, key)
        assert searches == [RouteName.IIIF_FILE]
