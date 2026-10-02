"""Tests for the mapping of errors to RFC 9457 problem documents."""

from http import HTTPStatus
from typing import TYPE_CHECKING, NamedTuple

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.api.problems import NOT_FOUND_DETAIL, UNEXPECTED_DETAIL
from bookreviver.domain.enums import UploadProblem
from bookreviver.domain.errors import (
    ConcurrentChangeError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    UnsupportedSourceError,
    UploadRejectedError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    import httpx
    from fastapi import FastAPI

pytestmark = pytest.mark.anyio

PROBLEM_MEDIA_TYPE: str = 'application/problem+json'
FAILING_PATH: str = '/test/fail'
SECRET_DETAIL: str = 'password=hunter2 in connection string'
CONTENT_TYPE_HEADER: str = 'content-type'
# Messages of domain errors, which reach the client unchanged as the problem detail
FORBIDDEN_MESSAGE: str = 'not yours'
CONFLICT_MESSAGE: str = 'import running'
UNSUPPORTED_MESSAGE: str = 'broken pdf'
CONCURRENT_MESSAGE: str = 'The pages changed while this ran. The book now shows them as they are. Try again.'


class ProblemCase(NamedTuple):
    """An error raised by a route and the problem it must produce.

    :ivar error: Exception the route raises.
    :ivar status: HTTP status the problem must carry.
    :ivar detail: Detail the problem must show, which never leaks an internal message.
    """

    error: Exception
    status: HTTPStatus
    detail: str


@pytest.fixture
def fx_failing_route(fx_app: FastAPI) -> Callable[[Exception], None]:
    """Return a function that makes ``FAILING_PATH`` raise the given error.

    :param fx_app: The running application the route is added to.
    :type fx_app: FastAPI
    :returns: Function adding a route at ``FAILING_PATH`` that raises its argument.
    :rtype: Callable[[Exception], None]
    """

    def install(error: Exception) -> None:
        """Add a route at ``FAILING_PATH`` that raises ``error``.

        :param error: Exception the route raises on every request.
        :type error: Exception
        """

        def fail() -> None:
            """Raise the installed error.

            :raises Exception: The error given to ``install``.
            """
            raise error

        fx_app.add_api_route(FAILING_PATH, fail)

    return install


class TestProblemHandler:
    """Tests for problem_handler()."""

    @pytest.mark.parametrize(
        'case',
        [
            ProblemCase(NotFoundError('internal-id-42'), HTTPStatus.NOT_FOUND, NOT_FOUND_DETAIL),
            ProblemCase(PermissionDeniedError(FORBIDDEN_MESSAGE), HTTPStatus.FORBIDDEN, FORBIDDEN_MESSAGE),
            ProblemCase(ConflictError(CONFLICT_MESSAGE), HTTPStatus.CONFLICT, CONFLICT_MESSAGE),
            ProblemCase(ConcurrentChangeError(), HTTPStatus.CONFLICT, CONCURRENT_MESSAGE),
            ProblemCase(UnsupportedSourceError(UNSUPPORTED_MESSAGE), HTTPStatus.BAD_REQUEST, UNSUPPORTED_MESSAGE),
            ProblemCase(
                UploadRejectedError(UploadProblem.UNSUPPORTED_TYPE),
                HTTPStatus.BAD_REQUEST,
                UploadProblem.UNSUPPORTED_TYPE.label,
            ),
            ProblemCase(
                UploadRejectedError(UploadProblem.TOO_LARGE),
                HTTPStatus.CONTENT_TOO_LARGE,
                UploadProblem.TOO_LARGE.label,
            ),
            ProblemCase(
                UploadRejectedError(UploadProblem.TOO_MANY_FILES),
                HTTPStatus.CONTENT_TOO_LARGE,
                UploadProblem.TOO_MANY_FILES.label,
            ),
            ProblemCase(RuntimeError(SECRET_DETAIL), HTTPStatus.INTERNAL_SERVER_ERROR, UNEXPECTED_DETAIL),
        ],
        ids=[
            'not-found',
            'forbidden',
            'conflict',
            'concurrent-change',
            'unsupported',
            'upload-rule',
            'too-large',
            'too-many-files',
            'unhandled',
        ],
    )
    async def test_error_becomes_problem(
        self, fx_client: httpx.AsyncClient, fx_failing_route: Callable[[Exception], None], case: ProblemCase
    ) -> None:
        """Verify each error answers with its status, a problem document and a detail safe to show.

        :param fx_client: HTTP client talking to the application in-process.
        :type fx_client: httpx.AsyncClient
        :param fx_failing_route: Function making ``FAILING_PATH`` raise a given error.
        :type fx_failing_route: Callable[[Exception], None]
        :param case: Error to raise and the problem it must produce.
        :type case: ProblemCase
        """
        fx_failing_route(case.error)
        response = await fx_client.get(FAILING_PATH)
        body = response.json()
        expect(response.status_code == case.status)
        expect(response.headers[CONTENT_TYPE_HEADER].startswith(PROBLEM_MEDIA_TYPE))
        expect(body['status'] == case.status)
        expect(body['detail'] == case.detail)
        expect(SECRET_DETAIL not in response.text)
        assert_expectations()

    async def test_validation_error_is_a_problem(self, fx_app: FastAPI, fx_client: httpx.AsyncClient) -> None:
        """Verify request validation failures use the same problem format.

        :param fx_app: The running application the route is added to.
        :type fx_app: FastAPI
        :param fx_client: HTTP client talking to the application in-process.
        :type fx_client: httpx.AsyncClient
        """

        def typed(number: int) -> int:
            """Return the number, so FastAPI validates the query parameter as an integer.

            :param number: Query parameter that must parse as an integer.
            :type number: int
            :returns: The same number.
            :rtype: int
            """
            return number

        fx_app.add_api_route(FAILING_PATH, typed)
        response = await fx_client.get(FAILING_PATH, params={'number': 'not-a-number'})
        expect(response.status_code == HTTPStatus.UNPROCESSABLE_CONTENT)
        expect(response.headers[CONTENT_TYPE_HEADER].startswith(PROBLEM_MEDIA_TYPE))
        assert_expectations()
