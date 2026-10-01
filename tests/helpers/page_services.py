"""Building the page service of a test over in-memory persistence and the local asset store."""

from typing import TYPE_CHECKING, override

import anyio

from bookreviver.adapters.imaging import VipsBlankPageMaker
from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.api.routing import IIIF_ROOT
from bookreviver.ports.imaging import Tiler
from bookreviver.services.base_versions import BaseVersions
from bookreviver.services.pages import PageImaging, PageRuntime, PageService

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.ports.imaging import BlankPageMaker
    from bookreviver.ports.ordering import OrderKeys
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
    database: InMemoryDatabase,
    assets: AssetStore,
    runtime: tuple[EventPublisher, Clock, JobQueue],
    *,
    order_keys: OrderKeys | None = None,
    blank_maker: BlankPageMaker | None = None,
) -> PageService:
    """Build a page service in a new unit of work, as a new request or job would get one.

    :param database: In-memory database the unit of work opens over.
    :type database: InMemoryDatabase
    :param assets: Asset store the service writes and reads files through.
    :type assets: AssetStore
    :param runtime: The publisher, the clock and the job queue the service reports through.
    :type runtime: tuple[EventPublisher, Clock, JobQueue]
    :param order_keys: Order keys to build keys with, or None for the fractional-indexing adapter.
    :type order_keys: OrderKeys | None
    :param blank_maker: Maker of blank leaves, or None for the libvips adapter.
    :type blank_maker: BlankPageMaker | None
    :returns: The service, whose tiler is a fake that writes token files.
    :rtype: PageService
    """
    publisher, clock, queue = runtime
    return PageService(
        uow=InMemoryUnitOfWork(database),
        assets=assets,
        runtime=PageRuntime(
            publisher=publisher, clock=clock, order_keys=order_keys or FractionalOrderKeys(), queue=queue
        ),
        imaging=PageImaging(
            base_versions=BaseVersions(assets=assets, tiler=FakeTiler(), iiif_root=IIIF_ROOT),
            blank_maker=blank_maker or VipsBlankPageMaker(),
        ),
    )
