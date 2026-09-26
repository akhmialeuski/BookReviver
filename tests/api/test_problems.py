"""Tests for the mapping of errors to RFC 9457 problem documents."""

from http import HTTPStatus
from typing import TYPE_CHECKING, NamedTuple

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.api.problems import NOT_FOUND_DETAIL, UNEXPECTED_DETAIL
from bookreviver.domain.enums import UploadProblem
from bookreviver.domain.errors import (
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


class ProblemCase(NamedTuple):
    """An error raised by a route and the problem it must produce."""

    error: Exception
    status: HTTPStatus
    detail: str


@pytest.fixture
def fx_failing_route(fx_app: FastAPI) -> Callable[[Exception], None]:
    """Return a function that makes ``FAILING_PATH`` raise the given error."""

    def install(error: Exception) -> None:
        def fail() -> None:
            raise error

        fx_app.add_api_route(FAILING_PATH, fail)

    return install


class TestProblemHandler:
    """Tests for problem_handler()."""

    @pytest.mark.parametrize(
        'case',
        [
            ProblemCase(NotFoundError('internal-id-42'), HTTPStatus.NOT_FOUND, NOT_FOUND_DETAIL),
            ProblemCase(PermissionDeniedError('not yours'), HTTPStatus.FORBIDDEN, 'not yours'),
            ProblemCase(ConflictError('import running'), HTTPStatus.CONFLICT, 'import running'),
            ProblemCase(UnsupportedSourceError('broken pdf'), HTTPStatus.BAD_REQUEST, 'broken pdf'),
            ProblemCase(
                UploadRejectedError(UploadProblem.MIXED_TYPES), HTTPStatus.BAD_REQUEST, UploadProblem.MIXED_TYPES.label
            ),
            ProblemCase(
                UploadRejectedError(UploadProblem.TOO_LARGE),
                HTTPStatus.CONTENT_TOO_LARGE,
                UploadProblem.TOO_LARGE.label,
            ),
            ProblemCase(RuntimeError(SECRET_DETAIL), HTTPStatus.INTERNAL_SERVER_ERROR, UNEXPECTED_DETAIL),
        ],
        ids=['not-found', 'forbidden', 'conflict', 'unsupported', 'upload-rule', 'too-large', 'unhandled'],
    )
    async def test_error_becomes_problem(
        self, fx_client: httpx.AsyncClient, fx_failing_route: Callable[[Exception], None], case: ProblemCase
    ) -> None:
        """Verify each error answers with its status, a problem document and a detail safe to show."""
        fx_failing_route(case.error)
        response = await fx_client.get(FAILING_PATH)
        body = response.json()
        expect(response.status_code == case.status)
        expect(response.headers['content-type'].startswith(PROBLEM_MEDIA_TYPE))
        expect(body['status'] == case.status)
        expect(body['detail'] == case.detail)
        expect(SECRET_DETAIL not in response.text)
        assert_expectations()

    async def test_validation_error_is_a_problem(self, fx_app: FastAPI, fx_client: httpx.AsyncClient) -> None:
        """Verify request validation failures use the same problem format."""

        def typed(number: int) -> int:
            return number

        fx_app.add_api_route(FAILING_PATH, typed)
        response = await fx_client.get(FAILING_PATH, params={'number': 'not-a-number'})
        expect(response.status_code == HTTPStatus.UNPROCESSABLE_CONTENT)
        expect(response.headers['content-type'].startswith(PROBLEM_MEDIA_TYPE))
        assert_expectations()
