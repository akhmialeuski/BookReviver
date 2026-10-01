"""Error hierarchy of the domain; the API maps each class to one HTTP problem."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bookreviver.domain.enums import UploadProblem


class DomainError(Exception):
    """Base of every error a use case reports to its caller."""


class InvalidIdentifierError(DomainError, ValueError):
    """A book identifier does not follow the rules of its scheme.

    It is also a ``ValueError``, so Pydantic turns it into a validation error when a schema calls the rule.
    """


class NotFoundError(DomainError):
    """The requested entity does not exist or is not visible to the acting account."""


class PermissionDeniedError(DomainError):
    """The acting account may not perform the operation."""


class ConflictError(DomainError):
    """The operation conflicts with the current state, for example a running import."""


class UnsupportedTransformError(DomainError):
    """A point cannot be mapped through a transform, such as one that follows a stored mesh."""


class InvalidParametersError(DomainError):
    """The parameters of a processing step do not follow the schema of its processor."""


class UnsupportedSourceError(DomainError):
    """The files of a source are not a readable source of its kind, such as a damaged PDF or image file."""


class UploadRejectedError(DomainError):
    """The uploaded file set breaks an upload rule.

    :ivar problem: The rule the upload breaks, whose label is the error message.
    """

    def __init__(self, problem: UploadProblem) -> None:
        """Report the broken rule.

        :param problem: The rule the upload breaks.
        :type problem: UploadProblem
        """
        super().__init__(problem.label)
        self.problem = problem
