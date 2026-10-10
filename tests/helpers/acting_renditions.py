"""A writer of the files of a version that lets another request act while it writes, as the real writer would let one."""

from typing import TYPE_CHECKING, override
from uuid import UUID

from bookreviver.domain.ids import PageId
from tests.helpers.fake_processing import FakeRenditionWriter

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from bookreviver.domain.enums import ColorMode, Rendition
    from bookreviver.domain.values import RenditionInfo

PAGES_DIRECTORY: str = 'pages'


class ActingRenditionWriter(FakeRenditionWriter):
    """A writer that lets something else happen in the middle of the first version, as another request would.

    The job holds no block while it writes files, so what ``act`` commits is what the job's own block finds afterwards.

    :ivar attempts: Number of versions it was asked to write.
    """

    def __init__(self, act: Callable[[PageId], Awaitable[None]]) -> None:
        """Run ``act`` while the first version is being written.

        :param act: Coroutine function called with the identifier of the page whose version is being written.
        :type act: Callable[[PageId], Awaitable[None]]
        """
        super().__init__()
        self.attempts = 0
        self._act = act

    @override
    async def write(self, image: Path, target_dir: Path, *, full: Rendition, color_mode: ColorMode) -> RenditionInfo:
        """Act in the middle of the first version, and write the files as the fake writer does.

        :param image: Image to write.
        :type image: Path
        :param target_dir: Directory to create, which lies under the directory of the page the version belongs to.
        :type target_dir: Path
        :param full: Format of the ``full`` image.
        :type full: Rendition
        :param color_mode: Colour of the image.
        :type color_mode: ColorMode
        :returns: What the fake writer returns.
        :rtype: RenditionInfo
        """
        self.attempts += 1
        if self.attempts == 1:
            await self._act(PageId(UUID(target_dir.parts[target_dir.parts.index(PAGES_DIRECTORY) + 1])))
        return await super().write(image, target_dir, full=full, color_mode=color_mode)
