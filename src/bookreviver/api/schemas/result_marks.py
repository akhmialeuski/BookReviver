"""Schema of the mark and the comment a request sets on a result."""

from bookreviver.api.schemas.base import RequestModel
from bookreviver.api.schemas.types import LongText
from bookreviver.domain.enums import ResultMark
from bookreviver.domain.values import ResultNote


class ResultMarkForm(RequestModel):
    """The mark and the comment of a result from now on, which replace both.

    :ivar mark: Good or bad, or None for a result without a mark.
    :ivar comment: The comment, one line or several, or empty for none.
    """

    mark: ResultMark | None
    comment: LongText = ''

    def to_note(self) -> ResultNote:
        """Return the pair as the domain states it.

        :returns: The mark and the comment.
        :rtype: ResultNote
        """
        return ResultNote(mark=self.mark, comment=self.comment)
