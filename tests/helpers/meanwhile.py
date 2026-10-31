"""Letting another change of a book land while a job computes, which is where a second request really lands.

A job reads and computes outside any block and stores its result in a short block that reads again what it went by.
The runner calls the writer of renditions after a processor ran and before that block, so a change made through a
second catalogue database from there lands exactly in the window the block has to close.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    import pytest

    from bookreviver.domain.events import DomainEvent
    from bookreviver.domain.images import ColorMode, Rendition, RenditionInfo
    from tests.helpers.processing import ProcessingKit


def change_while_writing(
    kit: ProcessingKit, monkeypatch: pytest.MonkeyPatch, change: Callable[[], Awaitable[None]]
) -> None:
    """Run a change once, the first time the kit's writer of renditions is asked to write the files of a version.

    The writer is the collaborator between the processor and the block that stores the version, so the job has read
    everything it goes by, and holds no block, when the change runs. The write that was asked for goes on after it.

    :param kit: The processing kit whose writer is wrapped.
    :type kit: ProcessingKit
    :param monkeypatch: Fixture that restores the writer when the test ends.
    :type monkeypatch: pytest.MonkeyPatch
    :param change: The change to make through a catalogue database of its own.
    :type change: Callable[[], Awaitable[None]]
    """
    write = kit.writer.write
    fired = False

    async def written_after_the_change(
        image: Path, target_dir: Path, *, full: Rendition, color_mode: ColorMode
    ) -> RenditionInfo:
        """Make the change on the first call, then write as the wrapped writer does.

        :param image: Image to copy.
        :type image: Path
        :param target_dir: Directory to create.
        :type target_dir: Path
        :param full: Format of the ``full`` image.
        :type full: Rendition
        :param color_mode: Colour of the image.
        :type color_mode: ColorMode
        :returns: What the wrapped writer returned.
        :rtype: RenditionInfo
        """
        nonlocal fired
        if not fired:
            fired = True
            await change()
        return await write(image, target_dir, full=full, color_mode=color_mode)

    monkeypatch.setattr(kit.writer, 'write', written_after_the_change)


def change_when_published(
    kit: ProcessingKit, monkeypatch: pytest.MonkeyPatch, change: Callable[[], Awaitable[None]]
) -> None:
    """Run a change once, right after the kit's event bus has been given the first event.

    A use case that cancels a preview announces it before it opens its own block, so a change made then lands between
    the checks the use case made outside the block and the block that decides.

    :param kit: The processing kit whose event bus runs the change after its first delivery.
    :type kit: ProcessingKit
    :param monkeypatch: Fixture that restores the bus when the test ends.
    :type monkeypatch: pytest.MonkeyPatch
    :param change: The change to make through a catalogue database of its own.
    :type change: Callable[[], Awaitable[None]]
    """
    fired = False

    async def change_once(_event: DomainEvent) -> None:
        """Make the change after the first event was delivered, and nothing after later ones.

        :param _event: The event just delivered, which the change does not need.
        :type _event: DomainEvent
        """
        nonlocal fired
        if not fired:
            fired = True
            await change()

    monkeypatch.setattr(kit.events, 'after_publish', change_once)
