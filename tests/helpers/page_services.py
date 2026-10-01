"""Building the page service of a test over in-memory persistence and the local asset store."""

from typing import TYPE_CHECKING, override

import anyio

from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
from bookreviver.api.routing import IIIF_ROOT
from bookreviver.plugins.blank import BlankPage
from bookreviver.plugins.split_none import SplitNone
from bookreviver.ports.imaging import Tiler
from bookreviver.services.base_versions import BaseVersions
from bookreviver.services.pages import PageImaging, PageRuntime, PageService
from bookreviver.services.steps import StepRunner
from tests.helpers.fake_processing import FakeCatalogue, FakeRenditionWriter

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.ports.imaging import RenditionWriter
    from bookreviver.ports.ordering import OrderKeys
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.runtime import Clock, EventPublisher, JobQueue
    from bookreviver.ports.storage import AssetStore

# What the fake tiler writes, so a test can tell that a rendition of a version was cut
PREVIEW_CONTENT: bytes = b'preview'
THUMBNAIL_CONTENT: bytes = b'thumbnail'
INFO_NAME: str = 'info.json'


class FakeTiler(Tiler):
    """A tiler that writes a token file for every rendition and cuts nothing, so a test needs no real image.

    :ivar cut: Images it was asked to cut, in order, whatever the rendition.
    """

    def __init__(self) -> None:
        """Start with nothing cut."""
        self.cut: list[Path] = []

    @override
    async def tile(self, image: Path, target_dir: Path, *, resource_id: str) -> None:
        """Write a directory holding an ``info.json`` that names the resource.

        :param image: Image to cut.
        :type image: Path
        :param target_dir: Directory to create.
        :type target_dir: Path
        :param resource_id: Address of the pyramid, written into the ``info.json``.
        :type resource_id: str
        """
        self.cut.append(image)
        directory = anyio.Path(target_dir)
        await directory.mkdir(parents=True)
        await (directory / INFO_NAME).write_text(resource_id)

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
    uow: UnitOfWork,
    assets: AssetStore,
    runtime: tuple[EventPublisher, Clock, JobQueue],
    *,
    order_keys: OrderKeys | None = None,
    renditions: RenditionWriter | None = None,
) -> PageService:
    """Build a page service over a unit of work, as a new request or job would get one.

    :param uow: Unit of work of the service, usually a new in-memory one over the database of the test.
    :type uow: UnitOfWork
    :param assets: Asset store the service writes and reads files through.
    :type assets: AssetStore
    :param runtime: The publisher, the clock and the job queue the service reports through.
    :type runtime: tuple[EventPublisher, Clock, JobQueue]
    :param order_keys: Order keys to build keys with, or None for the fractional-indexing adapter.
    :type order_keys: OrderKeys | None
    :param renditions: Writer of the files of a version, or None for one that copies the image and writes tokens.
    :type renditions: RenditionWriter | None
    :returns: The service, whose processors are the real ``split.none`` and ``pages.blank``, and whose tiler and writer of
              renditions write token files.
    :rtype: PageService
    """
    publisher, clock, queue = runtime
    tiler = FakeTiler()
    return PageService(
        uow=uow,
        assets=assets,
        runtime=PageRuntime(
            publisher=publisher, clock=clock, order_keys=order_keys or FractionalOrderKeys(), queue=queue
        ),
        imaging=PageImaging(
            base_versions=BaseVersions(assets=assets, tiler=tiler, iiif_root=IIIF_ROOT),
            runner=StepRunner(
                assets=assets,
                catalogue=FakeCatalogue([SplitNone(), BlankPage()]),
                renditions=renditions or FakeRenditionWriter(),
                tiler=tiler,
                iiif_root=IIIF_ROOT,
            ),
        ),
    )
