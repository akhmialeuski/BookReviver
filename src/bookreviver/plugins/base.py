"""The base of the built-in processors, whose parameters are a Pydantic model.

A processor declares its parameters as a model, and this base turns the model into the JSON Schema of its spec and into
the check ``validate_params`` makes, so the form the interface draws and the check the service runs come from one
declaration and cannot drift. Pydantic is allowed here because the rule that keeps it out applies to the core alone, and
a plugin that checks its parameters another way implements ``Processor`` without this class.

``params_model`` forbids unknown fields, so a misspelt parameter is an error and not a silent default.
"""

from abc import ABC
from typing import TYPE_CHECKING, ClassVar, override

from pydantic import BaseModel, ConfigDict, ValidationError

from bookreviver.domain.errors import InvalidParametersError
from bookreviver.ports.processing import Processor

if TYPE_CHECKING:
    from bookreviver.domain.values import MetadataMap


class Params(BaseModel):
    """The base of the parameters of a processor, which refuses a parameter it does not declare."""

    model_config = ConfigDict(extra='forbid')


class ModelProcessor(Processor, ABC):
    """A processor that checks its parameters with a Pydantic model.

    :ivar params_model: The model of the parameters, whose JSON Schema the processor's spec carries.
    """

    params_model: ClassVar[type[Params]]

    @override
    def validate_params(self, raw: MetadataMap) -> MetadataMap:
        """Check the parameters with the model and fill in its defaults.

        :param raw: Parameters as a recipe or a form gives them.
        :type raw: MetadataMap
        :returns: The parameters with every default filled in, as plain JSON data.
        :rtype: MetadataMap
        :raises InvalidParametersError: If a parameter is missing, unknown or out of its range.
        """
        try:
            return self.params_model.model_validate(raw).model_dump(mode='json')
        except ValidationError as error:
            err_msg = f'The parameters of {self.spec.key} are not valid: {error}'
            raise InvalidParametersError(err_msg) from error
