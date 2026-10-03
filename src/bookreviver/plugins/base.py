"""The base of the built-in processors, whose parameters are a Pydantic model.

A processor declares its parameters as a model, and this base turns the model into the JSON Schema of its spec and into
the check ``validate_params`` makes, so the form the interface draws and the check the service runs come from one
declaration and cannot drift. Pydantic is allowed here because the rule that keeps it out applies to the core alone, and
a plugin that checks its parameters another way implements ``Processor`` without this class.

``params_model`` forbids unknown fields, so a misspelt parameter is an error and not a silent default.
"""

from abc import ABC
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, ClassVar, get_args, override

from pydantic import BaseModel, ConfigDict, Discriminator, RootModel, ValidationError

from bookreviver.domain.errors import InvalidParametersError
from bookreviver.ports.processing import Processor

if TYPE_CHECKING:
    from enum import StrEnum

    from bookreviver.domain.values import MetadataMap

# The field of the parameters of a processor with several methods that names the method, and so the parameters it takes
METHOD_KEY: str = 'method'
# The label of that field in the form of the processor, though the choice of the method is drawn by the form itself
METHOD_TITLE: str = 'Method'


def leave_out_description(schema: dict[str, Any]) -> None:
    """Leave the docstring of a model out of its JSON Schema.

    The description of a model is its docstring, which is written for the code, and a form that draws the model of a
    method would show it to the reader. The description of each field is written for the reader and stays.

    :param schema: The JSON Schema of the model, which is changed in place.
    :type schema: dict[str, Any]
    """
    schema.pop('description', None)


class Params(BaseModel):
    """The base of the parameters of a processor, which refuses a parameter it does not declare."""

    model_config = ConfigDict(extra='forbid', json_schema_extra=leave_out_description)


def method_discriminator(default: StrEnum) -> Discriminator:
    """Make the discriminator that reads the method of a processor's parameters, taking the default when none is named.

    The parameters of a processor with several methods are a union of one model for each method, which the JSON Schema
    states as a ``oneOf`` so the form shows the fields of the chosen method only. A recipe stored before the processor
    had methods names none, and is read as the default method, so it keeps its behaviour.

    :param default: The method of parameters that name none.
    :type default: StrEnum
    :returns: The discriminator, to put in ``Annotated`` over a union of models tagged with the values of the methods.
    :rtype: Discriminator
    """

    def read(value: object) -> str | None:
        """Read the method out of the parameters, as a mapping or as a model.

        :param value: The parameters as they are given or as a model holds them.
        :type value: object
        :returns: The method, or None when a model has none.
        :rtype: str | None
        """
        if isinstance(value, Mapping):
            return str(value.get(METHOD_KEY, default))
        method = getattr(value, METHOD_KEY, None)
        return None if method is None else str(method)

    return Discriminator(read)


class ModelProcessor(Processor, ABC):
    """A processor that checks its parameters with a Pydantic model.

    :ivar params_model: The model of the parameters, whose JSON Schema the processor's spec carries. It is a root model
                        over a union for a processor with several methods.
    """

    params_model: ClassVar[type[BaseModel]]

    @classmethod
    def parameter_models(cls) -> tuple[type[BaseModel], ...]:
        """List the models whose fields make up the parameters: the model itself, or one for each method.

        :returns: The models of the parameters, one for a processor with a single method.
        :rtype: tuple[type[BaseModel], ...]
        """
        if not issubclass(cls.params_model, RootModel):
            return (cls.params_model,)
        # Pydantic keeps the discriminator in the metadata of the field, so the annotation is the union of the models
        union = cls.params_model.model_fields['root'].annotation
        models: list[type[BaseModel]] = [get_args(member)[0] for member in get_args(union)]
        return tuple(models)

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
