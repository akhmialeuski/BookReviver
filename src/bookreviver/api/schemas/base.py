"""Base classes carrying the configuration every request and response schema shares."""

from pydantic import BaseModel, ConfigDict


class RequestModel(BaseModel):
    """Input schema: unknown fields are rejected and surrounding whitespace is stripped."""

    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True, frozen=True)


class ResponseModel(BaseModel):
    """Output schema, built straight from a domain object's attributes with ``model_validate``."""

    model_config = ConfigDict(from_attributes=True, frozen=True)
