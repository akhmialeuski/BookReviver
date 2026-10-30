"""The recipes every stage of a new book starts with, read from a JSON file.

The file is ``recipes.json`` in this package, or the file the ``processing.recipes_file`` setting names. Changing a
default recipe is an edit of the file, not of the code. The file maps a stage, then a kind of page (``RecipeKind``), to
a template: ``processor_keys``, the keys of the processors of the steps in the order they run; ``params``, the
parameters of some of the steps by the key of their processor, which the defaults of the processor fill out; and
``off``, the keys of the steps that are in the recipe but switched off. Stages and kinds are written as the enums store
them, and so are the values of the parameters.

The file is validated when the application starts, and a file that does not fit stops the start.

JSON holds no comments, so the reasons for the shipped recipes are written here.

A page of text is followed along its lines, so its geometry is deskewed by the projection of the ink on the rows and
dewarped by the curves of the lines of text. A blank page is processed as a page of text, since a scan of a blank page
has the paper and the specks of a page of text. A picture is not followed along lines of text, so it is deskewed by the
long straight lines of rules and frames and dewarped by the edges of the sheet, and it keeps its tones, which is why
its cleanup makes it grayscale where a page of text is made black and white.

The steps of the Cleanup stage run in the order the file lists them. The page is made black and white first, so that
what is left of the dust is separate small spots. These are then removed, the strokes are made thinner or thicker, and
the eraser of the user comes last.
"""

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel, ValidationError

from bookreviver.domain.pages import RecipeKind
from bookreviver.domain.stages import Stage
from bookreviver.services.recipes import DefaultRecipes, RecipeTemplate

if TYPE_CHECKING:
    from pathlib import Path

INVALID_RECIPES_FILE: str = 'The file of default recipes {path} does not fit the schema of the recipes.'


class TemplateModel(BaseModel):
    """The steps of a recipe a stage starts with for one kind of page, as the file writes them.

    :ivar processor_keys: Keys of the processors of the steps, which run with their default parameters unless
                          ``params`` gives others.
    :ivar params: Parameters of some of the steps, by the key of the processor.
    :ivar off: Keys of the processors whose steps are in the recipe but switched off.
    """

    model_config = ConfigDict(extra='forbid', frozen=True)

    processor_keys: tuple[str, ...] = Field(min_length=1)
    params: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)
    off: frozenset[str] = frozenset()


class RecipesFile(RootModel[dict[Stage, dict[RecipeKind, TemplateModel]]]):
    """The whole file: the templates by stage and by kind of page."""


def load_default_recipes(path: Path) -> DefaultRecipes:
    """Read the file of default recipes.

    :param path: The JSON file.
    :type path: Path
    :returns: The templates of the file.
    :rtype: DefaultRecipes
    :raises OSError: If the file cannot be read.
    :raises ValueError: If the file does not fit the schema of the recipes.
    """
    try:
        loaded = RecipesFile.model_validate_json(path.read_text(encoding='utf-8'))
    except ValidationError as error:
        raise ValueError(INVALID_RECIPES_FILE.format(path=path)) from error
    return DefaultRecipes(
        {
            stage: {
                kind: RecipeTemplate(processor_keys=template.processor_keys, params=template.params, off=template.off)
                for kind, template in templates.items()
            }
            for stage, templates in loaded.root.items()
        }
    )
