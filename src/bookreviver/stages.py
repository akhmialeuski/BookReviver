"""Processing stages of a book, in pipeline order; each one is viewable at any time."""

import enum
from typing import Self


class Stage(enum.StrEnum):
    """A step of the digitisation pipeline, identified in URLs by its value."""

    IMPORT = 'import'
    PAGE_SPLIT = 'page-split'
    CLEANUP = 'cleanup'
    ALIGNMENT = 'alignment'
    RECOGNITION = 'recognition'
    LAYOUT = 'layout'

    @property
    def label(self) -> str:
        """The label shown on the stage tab."""
        return STAGE_LABELS[self]

    @classmethod
    def first(cls) -> Self:
        """Return the stage a project opens on."""
        return next(iter(cls))


STAGE_LABELS: dict[Stage, str] = {
    Stage.IMPORT: 'Import',
    Stage.PAGE_SPLIT: 'Page split',
    Stage.CLEANUP: 'Cleanup',
    Stage.ALIGNMENT: 'Alignment',
    Stage.RECOGNITION: 'Recognition',
    Stage.LAYOUT: 'Layout',
}
