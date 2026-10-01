"""The base versions of pages: the first version of a page, which holds the page's own copy of its image.

A page cut from a whole scan, with the page split skipped, has the base version ``split.none``, and a generated blank
leaf has ``pages.blank``. Until the plugin framework exists, the import and the page order stage write these versions
with this class, and the two processors replace it. It builds the versions, copies the ``full`` image of a scan into the
directory of a version, and cuts the preview, the thumbnail and the tile pyramid from a ``full`` image, for a scan as
well as for a version. The import and the binding of a scan to a placeholder both copy a scan, so they call the same
code, and a page made either way is the same to every later stage.

Every base version records the size of its image in its data, under the keys of ``VersionData``, which gives a blank
leaf the median size of the pages of the book.

The class touches no file itself: images are read and written through the asset store, and cut by the tiler.
"""

from functools import partial
from typing import TYPE_CHECKING

from bookreviver.domain.entities import PageVersion, VersionInputs
from bookreviver.domain.enums import Rendition, Stage, VersionState
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import PageSize, ProcessorRef, Renditions

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from bookreviver.domain.entities import Page, Scan
    from bookreviver.domain.ids import StorageKey
    from bookreviver.ports.imaging import Tiler
    from bookreviver.ports.storage import AssetStore

# The step that gives a page cut from a whole scan its base version while the page split is skipped, and the step that
# gives a generated blank leaf its base version, until their processors exist
SPLIT_NONE: ProcessorRef = ProcessorRef(key='split.none', version='1')
PAGES_BLANK: ProcessorRef = ProcessorRef(key='pages.blank', version='1')


class BaseVersions:
    """Builds the base versions of pages and writes their files."""

    def __init__(self, *, assets: AssetStore, tiler: Tiler, iiif_root: str) -> None:
        """Write the files of versions through the asset store, and cut them with the tiler.

        :param assets: Store of the derived files.
        :type assets: AssetStore
        :param tiler: Port cutting the pyramid, the preview and the thumbnail of an image.
        :type tiler: Tiler
        :param iiif_root: Path the IIIF routes are mounted at, which a pyramid's ``info.json`` names as its address.
        :type iiif_root: str
        """
        self._assets = assets
        self._tiler = tiler
        self._iiif_root = iiif_root

    @staticmethod
    def split_none(*, page: Page, scan: Scan, state: VersionState, moment: datetime) -> PageVersion:
        """Build the base version of a page that shows the whole of a scan, in the format of the scan's ``full``.

        :param page: Page showing the scan.
        :type page: Page
        :param scan: Scan whose ``full`` image the page copies, which already has its ``full`` format recorded.
        :type scan: Scan
        :param state: ``ready`` for a version whose files are written, ``pending`` for one a job will write.
        :type state: VersionState
        :param moment: Time the version is created.
        :type moment: datetime
        :returns: The version, with the size and resolution of the scan in its data.
        :rtype: PageVersion
        """
        facts = scan.facts
        size = PageSize(
            width_px=facts.width_px,
            height_px=facts.height_px,
            dpi=max(filter(None, (facts.dpi_x, facts.dpi_y)), default=None),
        )
        return PageVersion(
            id=VersionInputs(page_id=page.id, processor=SPLIT_NONE).identify(),
            page_id=page.id,
            stage=Stage.PAGE_SPLIT,
            processor=SPLIT_NONE,
            data=size.as_data(),
            renditions=Renditions(ready=state is VersionState.READY, full=scan.renditions.full),
            state=state,
            created_at=moment,
        )

    @staticmethod
    def blank(*, page: Page, size: PageSize, full: Rendition, moment: datetime) -> PageVersion:
        """Build the pending base version of a generated blank leaf, whose image a job writes.

        :param page: The blank page.
        :type page: Page
        :param size: Size and resolution of the leaf.
        :type size: PageSize
        :param full: Format of the leaf's ``full`` image, which the image policy gives a bilevel page.
        :type full: Rendition
        :param moment: Time the version is created.
        :type moment: datetime
        :returns: The pending version, with the size of the leaf in its data.
        :rtype: PageVersion
        """
        return PageVersion(
            id=VersionInputs(page_id=page.id, processor=PAGES_BLANK).identify(),
            page_id=page.id,
            stage=Stage.PAGE_ORDER,
            processor=PAGES_BLANK,
            data=size.as_data(),
            renditions=Renditions(ready=False, full=full),
            state=VersionState.PENDING,
            created_at=moment,
        )

    async def copy_scan(self, version: PageVersion, scan: Scan) -> None:
        """Give a version its own copy of a scan's ``full`` image, and cut its preview, thumbnail and pyramid.

        What an earlier attempt left in the directory of the version is removed first, since a stored file is never
        replaced.

        :param version: The base version, whose ``full`` format is the scan's.
        :type version: PageVersion
        :param scan: Scan to copy, whose renditions are ready.
        :type scan: Scan
        """
        keys = ProjectKeys(scan.project_id)
        full = scan.renditions.full
        of_version = partial(keys.version_rendition, version)
        await self._assets.delete_prefix(keys.version_directory(version))
        await self._assets.copy(keys.scan_rendition(scan, full), of_version(full))
        await self.derive(of_version, full=full)

    async def derive(self, key: Callable[[Rendition], StorageKey], *, full: Rendition) -> None:
        """Cut the preview, the thumbnail and the tile pyramid from the ``full`` image stored under the keys.

        :param key: Function giving the storage key of each rendition of one scan or one page version.
        :type key: Callable[[Rendition], StorageKey]
        :param full: Format the ``full`` image was written in, which names the file to cut from.
        :type full: Rendition
        """
        async with self._assets.readable(key(full)) as image:
            async with self._assets.writable(key(Rendition.PREVIEW)) as target:
                await self._tiler.preview(image, target)
            async with self._assets.writable(key(Rendition.THUMBNAIL)) as target:
                await self._tiler.thumbnail(image, target)
            tiles = key(Rendition.TILES)
            async with self._assets.writable(tiles) as target:
                await self._tiler.tile(image, target, resource_id=f'{self._iiif_root}/{tiles}')
