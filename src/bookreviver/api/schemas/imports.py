"""Schema of the multipart body of an upload, validated by Pydantic once the route has authorised the caller."""

from typing import Annotated

from fastapi import UploadFile
from pydantic import Field

from bookreviver.api.schemas.base import RequestModel


class UploadForm(RequestModel):
    """The form of an upload: the files to import, each one a source of its own.

    :ivar files: The uploaded files, at least one, from a directory or chosen one by one.
    """

    files: Annotated[list[UploadFile], Field(min_length=1, description='The files to import')]
