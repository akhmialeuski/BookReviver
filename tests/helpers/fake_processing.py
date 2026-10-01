"""Fakes of the processing ports: a catalogue of given processors, a rendition writer that copies, a queue that refuses.

The rendition writer copies the image it is given as ``full`` and writes token files for the preview and the
thumbnail, so no image library is needed to tell which files a version has and in which format its ``full`` was asked
for. The tokens are those of the fake tiler, so a test of either reads the same bytes.
"""

from typing import TYPE_CHECKING, override

import anyio

from bookreviver.domain.enums import Rendition
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.values import RenditionInfo
from bookreviver.ports.imaging import RenditionWriter
from bookreviver.ports.processing import ProcessorCatalog
from bookreviver.ports.runtime import JobQueue

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from bookreviver.domain.entities import Job
    from bookreviver.domain.enums import ColorMode
    from bookreviver.domain.values import ProcessorSpec
    from bookreviver.ports.processing import Processor

# What the fake writer says every image measures, and what it writes as the smaller files
IMAGE_SIZE_PX: int = 100
PREVIEW_TOKEN: bytes = b'preview'
THUMBNAIL_TOKEN: bytes = b'thumbnail'


class RefusingJobQueue(JobQueue):
    """A queue that refuses every job, as a broker that is down does."""

    @override
    async def enqueue(self, job: Job) -> None:
        """Refuse the job.

        :param job: The job handed to the queue.
        :type job: Job
        :raises RuntimeError: Always.
        """
        err_msg = f'The queue is down, so it cannot take {job.id}.'
        raise RuntimeError(err_msg)


class FakeCatalogue(ProcessorCatalog):
    """A catalogue of the processors it was given."""

    def __init__(self, processors: Sequence[Processor]) -> None:
        """Offer the given processors.

        :param processors: The processors, found by the keys of their specs.
        :type processors: Sequence[Processor]
        """
        self._processors = {processor.spec.key: processor for processor in processors}

    @override
    def get(self, key: str) -> Processor:
        """Return the processor with this key.

        :param key: Key of the processor.
        :type key: str
        :returns: The processor.
        :rtype: Processor
        :raises NotFoundError: If the catalogue has none with this key.
        """
        if (processor := self._processors.get(key)) is None:
            raise NotFoundError(key)
        return processor

    @override
    def specs(self) -> Sequence[ProcessorSpec]:
        """List the specs by key.

        :returns: The specs of the processors.
        :rtype: Sequence[ProcessorSpec]
        """
        return [self._processors[key].spec for key in sorted(self._processors)]


class FakeRenditionWriter(RenditionWriter):
    """A writer that copies the image as ``full`` and writes token files for the other two.

    :ivar calls: The format and the colour of every ``full`` it was asked to write, in order.
    """

    def __init__(self) -> None:
        """Start with nothing written."""
        self.calls: list[tuple[Rendition, ColorMode]] = []

    @override
    async def write(self, image: Path, target_dir: Path, *, full: Rendition, color_mode: ColorMode) -> RenditionInfo:
        """Write the three files of an image.

        :param image: Image to copy.
        :type image: Path
        :param target_dir: Directory to create.
        :type target_dir: Path
        :param full: Format of the ``full`` image.
        :type full: Rendition
        :param color_mode: Colour of the image.
        :type color_mode: ColorMode
        :returns: A fixed size and the format asked for.
        :rtype: RenditionInfo
        """
        self.calls.append((full, color_mode))
        directory = anyio.Path(target_dir)
        await directory.mkdir(parents=True)
        await (directory / full).write_bytes(await anyio.Path(image).read_bytes())
        await (directory / Rendition.PREVIEW).write_bytes(PREVIEW_TOKEN)
        await (directory / Rendition.THUMBNAIL).write_bytes(THUMBNAIL_TOKEN)
        return RenditionInfo(width_px=IMAGE_SIZE_PX, height_px=IMAGE_SIZE_PX, full=full)
