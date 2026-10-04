"""Schemas of the mark and the comment of a result: the pair a request sets, and a change of the log."""

from datetime import datetime

from bookreviver.api.schemas.base import RequestModel, ResponseModel
from bookreviver.api.schemas.types import LongText
from bookreviver.domain.enums import ResultMark
from bookreviver.domain.ids import PageVersionId
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


class ResultMarkChangeSchema(ResponseModel):
    """One change of the mark or the comment of a result.

    :ivar version_id: Version the change was made on.
    :ivar sequence: Place of the change in the log of the version, from one.
    :ivar mark_before: Mark before the change, or None.
    :ivar mark_after: Mark after the change, or None.
    :ivar comment_before: Comment before the change, or empty.
    :ivar comment_after: Comment after the change, or empty.
    :ivar created_at: When the change was made.
    """

    version_id: PageVersionId
    sequence: int
    mark_before: ResultMark | None
    mark_after: ResultMark | None
    comment_before: str
    comment_after: str
    created_at: datetime
