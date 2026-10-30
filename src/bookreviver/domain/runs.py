"""Runs of a stage: what a run does with the work pages have, what it may take away and what it came to."""

from attrs import frozen

from bookreviver.domain.labels import LabeledStrEnum


class RunOutcome(LabeledStrEnum):
    """What a run of a recipe came to on one page."""

    DONE = 'done', 'Processed'
    SKIPPED = 'skipped', 'Skipped, the page has no image to process'
    FAILED = 'failed', 'Failed'


class RunMode(LabeledStrEnum):
    """What a run of a stage does with the work the pages already have for its steps.

    The work is the settings a page changes for a step and the manual edit a step reads on it. A run keeps both unless
    it is asked to take both away, which is written to the history of each page like any other change, or to leave the
    pages that have either out of the run.
    """

    KEEP = 'keep', 'Keep the settings and edits of the pages'
    SKIP_OWN = 'skip-own-work', 'Leave out the pages with work of their own'
    DROP_OWN = 'drop-own-work', 'Drop the settings and edits of the pages'


@frozen(kw_only=True)
class RunImpact:
    """What a mode of a run would take away, counted over the pages and the steps the run goes over.

    :ivar mode: The mode of the run.
    :ivar pages: How many pages the run goes over.
    :ivar own_pages: How many of them have a manual edit or change at least one field on a step the run goes over,
                     which are the pages with work of their own.
    """

    mode: RunMode
    pages: int
    own_pages: int

    @property
    def affected(self) -> int:
        """How many pages lose work to the mode, which is none for a run that keeps it."""
        return self.own_pages if self.mode is RunMode.DROP_OWN else 0
