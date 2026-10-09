"""Error hierarchy of the domain; the API maps each class to one HTTP problem."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

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


class AnchorInsideMovedPagesError(ConflictError):
    """The page to put pages next to is one of the pages being moved, so the place is not defined.

    The message is a sentence for the person moving the pages, which the API sends as the detail of the problem.
    """

    def __init__(self) -> None:
        """Report the conflict with its fixed sentence."""
        super().__init__('The place chosen is one of the pages being moved. Choose a page that stays where it is.')


class ConcurrentChangeError(ConflictError):
    """A row was changed by another transaction after this one read it, so writing it would lose that change.

    The message is a sentence for the person whose request lost the race, which the API sends as the detail of the
    problem.
    """

    def __init__(self) -> None:
        """Report the conflict with its fixed sentence."""
        super().__init__('The pages changed while this ran. The book now shows them as they are. Try again.')


class BookBusyError(DomainError):
    """Another change of the same book held it for longer than a change waits, so this one did not start.

    The message is a sentence for the person whose request had to wait, which the API sends as the detail of the
    problem.
    """

    def __init__(self) -> None:
        """Report the wait with its fixed sentence."""
        super().__init__('The book is being changed by something else right now. Try again in a moment.')


class ReversedRangeError(ConflictError):
    """A numbering runs from a page that stands after its last page.

    The message is a sentence for the person numbering the pages, which the API sends as the detail of the problem.
    """

    def __init__(self) -> None:
        """Report the conflict with its fixed sentence."""
        super().__init__('The numbering runs from a later page to an earlier one.')


class NotAPlaceholderError(ConflictError):
    """A scan is bound to a page that is not a placeholder, which is the only kind of page that waits for a scan.

    The message is a sentence for the person binding the scan, which the API sends as the detail of the problem.
    """

    def __init__(self) -> None:
        """Report the conflict with its fixed sentence."""
        super().__init__('Only a missing page can take a scan.')


class ScanAlreadyInBookError(ConflictError):
    """A scan is bound to a placeholder while other pages of the book show it already.

    The message names those pages by their printed number or position, which the API sends as the detail of the
    problem.

    :ivar places: How each page that shows the scan is named to the person, in book order.
    """

    def __init__(self, places: Sequence[str]) -> None:
        """Report the conflict with the places of the pages that show the scan.

        :param places: Printed number or position of each page that shows the scan, such as ``p. 12``.
        :type places: Sequence[str]
        """
        noun = 'a page' if len(places) == 1 else 'pages'
        super().__init__(f'This scan is already {noun} of the book: {", ".join(places)}.')
        self.places = places


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
