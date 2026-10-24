"""Building the page service of a test over a unit of work of the test's database and the local asset store."""

from typing import TYPE_CHECKING, override

import anyio

from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
from bookreviver.plugins.blank import BlankPage
from bookreviver.plugins.split_none import SplitNone
from bookreviver.ports.imaging import Tiler
from bookreviver.services.base_versions import BaseVersions
from bookreviver.services.pages import PageImaging, PageRuntime, PageService
from bookreviver.services.steps import StepRunner
from tests.helpers.books_service import make_books_service
from tests.helpers.fake_processing import FakeCatalogue, FakeRenditionWriter
from tests.helpers.in_process_executor import InProcessStepExecutor

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.ports.imaging import RenditionWriter
    from bookreviver.ports.ordering import OrderKeys
    from bookreviver.ports.runtime import EventPublisher, JobBoard
    from tests.helpers.builders import BookOpener

# What the fake tiler writes, so a test can tell that a rendition of a version was cut
PREVIEW_CONTENT: bytes = b'preview'
THUMBNAIL_CONTENT: bytes = b'thumbnail'
INFO_NAME: str = 'info.json'
# The image information document of a fake pyramid, which the API fills in when it serves it
EMPTY_INFO: str = '{}'


class FakeTiler(Tiler):
    """A tiler that writes a token file for every rendition and cuts nothing, so a test needs no real image.

    :ivar cut: Images it was asked to cut, in order, whatever the rendition.
    """

    def __init__(self) -> None:
        """Start with nothing cut."""
        self.cut: list[Path] = []

    @override
    async def tile(self, image: Path, target_dir: Path) -> None:
        """Write a directory holding an empty ``info.json``.

        :param image: Image to cut.
        :type image: Path
        :param target_dir: Directory to create.
        :type target_dir: Path
        """
        self.cut.append(image)
        directory = anyio.Path(target_dir)
        await directory.mkdir(parents=True)
        await (directory / INFO_NAME).write_text(EMPTY_INFO)

    @override
    async def preview(self, image: Path, target: Path) -> None:
        """Write a token preview.

        :param image: Image to shrink.
        :type image: Path
        :param target: Path to write at.
        :type target: Path
        """
        self.cut.append(image)
        await anyio.Path(target).write_bytes(PREVIEW_CONTENT)

    @override
    async def thumbnail(self, image: Path, target: Path) -> None:
        """Write a token thumbnail.

        :param image: Image to shrink.
        :type image: Path
        :param target: Path to write at.
        :type target: Path
        """
        self.cut.append(image)
        await anyio.Path(target).write_bytes(THUMBNAIL_CONTENT)


def make_page_service(
    stores: BookOpener,
    runtime: tuple[EventPublisher, JobBoard],
    *,
    order_keys: OrderKeys | None = None,
    renditions: RenditionWriter | None = None,
) -> PageService:
    """Build a page service over a unit of work, as a new request or job would get one.

    :param stores: Opens the unit of work, the databases and the file stores of the service, over the test's own.
    :type stores: BookOpener
    :param runtime: The publisher and the job board the service reports through.
    :type runtime: tuple[EventPublisher, JobBoard]
    :param order_keys: Order keys to build keys with, or None for the fractional-indexing adapter.
    :type order_keys: OrderKeys | None
    :param renditions: Writer of the files of a version, or None for one that copies the image and writes tokens.
    :type renditions: RenditionWriter | None
    :returns: The service, whose processors are the real ``split.none`` and ``pages.blank``, and whose tiler and writer of
              renditions write token files.
    :rtype: PageService
    """
    publisher, board = runtime
    tiler = FakeTiler()
    return PageService(
        books=make_books_service(stores.uow(), databases=stores.databases, files=stores.files),
        runtime=PageRuntime(publisher=publisher, order_keys=order_keys or FractionalOrderKeys(), board=board),
        imaging=PageImaging(
            base_versions=BaseVersions(tiler=tiler),
            runner=StepRunner(
                executor=InProcessStepExecutor(FakeCatalogue([SplitNone(), BlankPage()])),
                renditions=renditions or FakeRenditionWriter(),
                tiler=tiler,
            ),
        ),
    )
