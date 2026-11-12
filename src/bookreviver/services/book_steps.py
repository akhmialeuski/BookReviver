"""Finding the steps of recipes whose plugin reads the whole book.

A plugin whose result on one page depends on the other pages has a half that reads the book (``BookStep``). The engine
never names such a plugin: a run, a plan of a run and the measure of the book ask here which steps have that half, and
the half itself what it leaves to the book.
"""

from typing import TYPE_CHECKING

from bookreviver.domain.errors import NotFoundError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.core.plugins import BookStep, ProcessorCatalog
    from bookreviver.domain.recipes import Recipe, Step


class BookSteps:
    """Finds the steps of recipes whose plugin reads the whole book."""

    def __init__(self, processors: ProcessorCatalog) -> None:
        """Find the plugins in a catalogue.

        :param processors: The processors the application can run.
        :type processors: ProcessorCatalog
        """
        self._processors = processors

    def of(self, step: Step) -> BookStep | None:
        """Give the half of the plugin of a step that reads the book.

        :param step: A step of a recipe.
        :type step: Step
        :returns: The half, or None for a step that needs one page only or whose processor is not installed.
        :rtype: BookStep | None
        """
        try:
            return self._processors.get(step.processor_key).book_step
        except NotFoundError:
            return None

    def position(self, steps: Sequence[Step]) -> int | None:
        """Find the first of some steps whose plugin reads the book.

        :param steps: Steps of a recipe.
        :type steps: Sequence[Step]
        :returns: The index of the step, or None when no step reads the book.
        :rtype: int | None
        """
        return next((index for index, step in enumerate(steps) if self.of(step) is not None), None)

    def leaves_to_book(self, recipe: Recipe, *, through_step: int | None = None) -> bool:
        """Tell whether a run of the recipe has a step that takes a field of its parameters from the book.

        A page run by such a recipe gets a result that what the step found on every page of the book decides, so its
        result changes when another page does. The parameters are read as the recipe stores them.

        :param recipe: Recipe of the stage.
        :type recipe: Recipe
        :param through_step: Index in the recipe of the last step the run goes over, or None for every step that is on.
        :type through_step: int | None
        :returns: Whether a step the run goes over leaves a field to the book.
        :rtype: bool
        """
        return any(
            (book_step := self.of(step)) is not None and book_step.leaves_to_book(step.params)
            for _, step in recipe.indexed_steps_through(through_step)
        )
