"""The single mapping from domain errors to RFC 9457 problems, installed by the application factory."""

from http import HTTPStatus
from typing import TYPE_CHECKING, ClassVar

from fastapi_problem.error import StatusProblem
from fastapi_problem.handler import new_exception_handler

from bookreviver.domain.errors import (
    BookBusyError,
    ConflictError,
    DomainError,
    InvalidArchiveError,
    InvalidParametersError,
    NotFoundError,
    PermissionDeniedError,
    UnsupportedSourceError,
    UnsupportedTransformError,
    UploadProblem,
    UploadRejectedError,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from logging import Logger

    from fastapi_problem.error import Problem
    from fastapi_problem.handler import ExceptionHandler
    from starlette.requests import Request

# The upload rules whose breach is answered as a content too large problem
OVERSIZED_UPLOADS: frozenset[UploadProblem] = frozenset({UploadProblem.TOO_LARGE, UploadProblem.TOO_MANY_FILES})
# The header that tells a client how long to wait before it asks again, which the problem of a busy book carries
RETRY_AFTER_HEADER: str = 'Retry-After'
NOT_FOUND_DETAIL: str = 'The requested resource does not exist.'
UNEXPECTED_DETAIL: str = 'An unexpected error occurred. It has been logged.'


class ApiProblem(StatusProblem):
    """A problem whose title is the standard reason phrase of its status.

    :cvar http_status: Status the problem answers with, which its title is the reason phrase of.
    :cvar RETRY_AFTER_SECONDS: Seconds the problem tells a client to wait before it asks again in the ``Retry-After``
                               header, or None for a problem that sends none.
    """

    http_status: ClassVar[HTTPStatus]
    RETRY_AFTER_SECONDS: ClassVar[int | None] = None

    def __init_subclass__(cls) -> None:
        """Derive the status, the title and the retry header of a concrete problem from its class attributes."""
        super().__init_subclass__()
        cls.status = cls.http_status
        cls.title = cls.http_status.phrase
        if cls.RETRY_AFTER_SECONDS is not None:
            # fastapi-problem's own attribute, which rfc9457 copies into the headers of every instance
            cls.headers_ = {RETRY_AFTER_HEADER: str(cls.RETRY_AFTER_SECONDS)}


class BadRequest(ApiProblem):
    """The request breaks a rule of the API."""

    http_status = HTTPStatus.BAD_REQUEST


class Unauthorized(ApiProblem):
    """The caller is not signed in."""

    http_status = HTTPStatus.UNAUTHORIZED


class Forbidden(ApiProblem):
    """The acting account may not do this."""

    http_status = HTTPStatus.FORBIDDEN


class NotFound(ApiProblem):
    """The resource does not exist or is not visible to the caller."""

    http_status = HTTPStatus.NOT_FOUND


class Conflict(ApiProblem):
    """The request conflicts with the current state."""

    http_status = HTTPStatus.CONFLICT


class UnprocessableContent(ApiProblem):
    """The request is well formed, but its content is not valid, such as the parameters of a processing step."""

    http_status = HTTPStatus.UNPROCESSABLE_CONTENT


class ContentTooLarge(ApiProblem):
    """The upload is larger than the server accepts."""

    http_status = HTTPStatus.CONTENT_TOO_LARGE


class ServiceUnavailable(ApiProblem):
    """The server cannot take the request now, but may be able to a moment later."""

    http_status = HTTPStatus.SERVICE_UNAVAILABLE
    # The 99th percentile of the wait for the write lock measured under load was 0.8 to 1.7 seconds, so after a second
    # the holder has most likely let go
    RETRY_AFTER_SECONDS = 1


class Unexpected(ApiProblem):
    """An unhandled error, reported without its message so internals never reach the client."""

    http_status = HTTPStatus.INTERNAL_SERVER_ERROR

    def __init__(self, *_details: object) -> None:
        """Create the problem with a fixed detail, whatever the error said.

        :param _details: Arguments fastapi-problem passes from the unhandled error, deliberately ignored.
        :type _details: object
        """
        super().__init__(UNEXPECTED_DETAIL)


# Each domain error class maps to one problem class; the detail is the error's own message
PROBLEM_BY_ERROR: dict[type[DomainError], type[ApiProblem]] = {
    NotFoundError: NotFound,
    PermissionDeniedError: Forbidden,
    ConflictError: Conflict,
    InvalidParametersError: UnprocessableContent,
    UnsupportedTransformError: Conflict,
    UnsupportedSourceError: BadRequest,
    UploadRejectedError: BadRequest,
    InvalidArchiveError: BadRequest,
    BookBusyError: ServiceUnavailable,
}


def _problem_for(error: DomainError) -> Problem:
    """Return the problem that reports ``error`` to an API client.

    :param error: Domain error a service raised.
    :type error: DomainError
    :returns: Problem of the error's class, whose detail is the error's message except for a missing resource.
    :rtype: Problem
    """
    if isinstance(error, UploadRejectedError) and error.problem in OVERSIZED_UPLOADS:
        return ContentTooLarge(error.problem.label)
    if isinstance(error, NotFoundError):
        # Never echo identifiers of resources the caller may not know exist
        return NotFound(NOT_FOUND_DETAIL)
    problem_class = next(problem for cls, problem in PROBLEM_BY_ERROR.items() if isinstance(error, cls))
    return problem_class(str(error))


def _handle(_handler: ExceptionHandler, _request: Request, error: DomainError) -> Problem:
    """Adapt ``_problem_for`` to fastapi-problem's handler signature.

    :param _handler: The exception handler calling this function, unused.
    :type _handler: ExceptionHandler
    :param _request: Request that raised the error, unused.
    :type _request: Request
    :param error: Domain error a service raised.
    :type error: DomainError
    :returns: Problem reporting the error.
    :rtype: Problem
    """
    return _problem_for(error)


def problem_handler(logger: Logger) -> ExceptionHandler:
    """Build the exception handler that answers every error with a problem document.

    :param logger: Logger receiving unhandled errors with their traceback.
    :type logger: Logger
    :returns: Handler registered once in ``app`` for every exception.
    :rtype: ExceptionHandler
    """
    handlers: dict[type[Exception], Callable[..., Problem]] = {DomainError: _handle}
    # The default wrapper would put ``str(exc)`` of an unhandled error into the response
    return new_exception_handler(logger=logger, handlers=handlers, unhandled_wrappers={'default': Unexpected})
