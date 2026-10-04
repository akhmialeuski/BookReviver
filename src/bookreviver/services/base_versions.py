"""The base versions of pages: the first version of a page, which holds the page's own copy of its image.

A page cut from a whole scan, with the page split skipped, has the base version ``split.none``, and a generated blank
leaf has ``pages.blank``. Both are processors, and this class only builds the rows of their versions: the identifier
comes from ``VersionInputs`` like that of every version, so equal work gets the same identifier, and the parameters of
a blank leaf are its size. A version is built pending, or ready when a use case has written its files already, and a
``StepRunner`` runs the processor and writes the files, which the import and the ``prepare-pages`` job both do, so a
page made either way is the same to every later stage.

It also cuts the preview, the thumbnail and the tile pyramid of a scan, which the import needs for the renditions of a
scan, from the ``full`` image stored under the keys of a scan.

The class touches no file itself: images are read and written through the asset store, and cut by the tiler.
"""

from typing import TYPE_CHECKING

from bookreviver.domain.entities import PageVersion, VersionInputs
from bookreviver.domain.enums import BlankParam, PaperFill, Rendition, Stage, VersionState
from bookreviver.domain.values import PageSize, ProcessorRef, Renditions

if TYPE_CHECKING:
    from collections.abc import Callable, Collection
    from datetime import datetime

    from bookreviver.domain.entities import Page, Scan
    from bookreviver.domain.ids import PageVersionId, StorageKey
    from bookreviver.ports.imaging import Tiler
    from bookreviver.ports.storage import AssetStore

# The step that gives a page cut from a whole scan its base version while the page split is skipped, and the step that
# gives a generated blank leaf its base version
SPLIT_NONE: ProcessorRef = ProcessorRef(key='split.none', version='1')
PAGES_BLANK: ProcessorRef = ProcessorRef(key='pages.blank', version='1')


class BaseVersions:
    """Builds the base versions of pages and cuts the files of scans."""

    def __init__(self, *, assets: AssetStore, tiler: Tiler, iiif_root: str) -> None:
        """Cut the files of scans through the asset store with the tiler.

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
    def blank(
        *,
        page: Page,
        size: PageSize,
        full: Rendition,
        moment: datetime,
        paper_from: Collection[PageVersionId] | None = None,
    ) -> PageVersion:
        """Build the pending base version of a generated blank leaf, whose image a job writes.

        A white leaf has the size alone for its parameters, as a leaf had before it could be anything else, so the
        identifiers of the leaves already stored stay as they are. A leaf of the colour of the paper names the versions
        of the pages it takes the paper from, which is what makes a leaf made from other pages another version.

        :param page: The blank page.
        :type page: Page
        :param size: Size and resolution of the leaf.
        :type size: PageSize
        :param full: Format of the leaf's ``full`` image, which the image policy gives a bilevel page.
        :type full: Rendition
        :param moment: Time the version is created.
        :type moment: datetime
        :param paper_from: Versions of the neighbouring pages whose paper the leaf takes, none for a white leaf. An
                           empty collection is a leaf of the paper with no page to take it from, which is white.
        :type paper_from: Collection[PageVersionId] | None
        :returns: The pending version, with the size of the leaf in its data.
        :rtype: PageVersion
        """
        params = size.as_data()
        if paper_from is not None:
            params = {**params, BlankParam.FILL: PaperFill.PAPER.value, BlankParam.PAPER_FROM: sorted(paper_from)}
        return PageVersion(
            id=VersionInputs(page_id=page.id, processor=PAGES_BLANK, params=params).identify(),
            page_id=page.id,
            stage=Stage.PAGE_ORDER,
            processor=PAGES_BLANK,
            params=params,
            data=size.as_data(),
            renditions=Renditions(ready=False, full=full),
            state=VersionState.PENDING,
            created_at=moment,
        )

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
