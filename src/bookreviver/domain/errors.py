"""Error hierarchy of the domain; the API maps each class to one HTTP problem."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bookreviver.domain.enums import UploadProblem


class DomainError(Exception):
    """Base of every error a use case reports to its caller."""


class NotFoundError(DomainError):
    """The requested entity does not exist or is not visible to the acting account."""


class PermissionDeniedError(DomainError):
    """The acting account may not perform the operation."""


class ConflictError(DomainError):
    """The operation conflicts with the current state, for example a running import."""


class UnsupportedSourceError(DomainError):
    """The uploaded files are not a readable PDF or image set."""


class UploadRejectedError(DomainError):
    """The uploaded file set breaks an upload rule."""

    def __init__(self, problem: UploadProblem) -> None:
        super().__init__(problem.label)
        self.problem = problem
