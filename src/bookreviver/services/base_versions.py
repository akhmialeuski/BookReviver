"""The base versions of pages: the first version of a page, which holds the page's own copy of its image.

A page cut from a whole scan, with the page split skipped, has a base version made by the processor of the role
``ProcessorRole.SCAN_COPY``, and a generated blank leaf has one made by the processor of the role
``ProcessorRole.BLANK_LEAF``. The catalogue names both, so nothing here knows which plugin fills a role or the version
it has. This class only builds the rows of their versions: the identifier
comes from ``VersionInputs`` like that of every version, so equal work gets the same identifier, and the parameters of
a blank leaf are its size. A version is built pending, or ready when a use case has written its files already, and a
``StepRunner`` runs the processor and writes the files, which the import and the ``prepare-pages`` job both do, so a
page made either way is the same to every later stage.

It also cuts the preview, the thumbnail and the tile pyramid of a scan, which the import needs for the renditions of a
scan, from the ``full`` image stored under the keys of a scan.

The class touches no file itself: images are read and written through the asset store, and cut by the tiler.
"""

from typing import TYPE_CHECKING

from bookreviver.domain.images import Rendition, Renditions
from bookreviver.domain.processors import ProcessorRole
from bookreviver.domain.step_settings import BlankParam, PaperFill
from bookreviver.domain.versions import PageSize, PageVersion, VersionInputs, VersionState

if TYPE_CHECKING:
    from collections.abc import Callable, Collection
    from datetime import datetime

    from bookreviver.core.plugins import ProcessorCatalog
    from bookreviver.domain.ids import PageVersionId, StorageKey
    from bookreviver.domain.pages import Page
    from bookreviver.domain.processors import ProcessorSpec
    from bookreviver.domain.sources import Scan
    from bookreviver.ports.imaging import Tiler
    from bookreviver.ports.storage import AssetStore


class BaseVersions:
    """Builds the base versions of pages and cuts the files of scans."""

    def __init__(self, *, tiler: Tiler, processors: ProcessorCatalog) -> None:
        """Cut the files of scans with the tiler, and name the processors of base versions by the catalogue.

        :param tiler: Port cutting the pyramid, the preview and the thumbnail of an image.
        :type tiler: Tiler
        :param processors: The processors the application can run, two of which make the base versions.
        :type processors: ProcessorCatalog
        """
        self._tiler = tiler
        self._processors = processors

    @property
    def scan_copy(self) -> ProcessorSpec:
        """The processor that gives a page cut from a whole scan its base version while the page split is skipped."""
        return self._processors.of_role(ProcessorRole.SCAN_COPY)

    @property
    def blank_leaf(self) -> ProcessorSpec:
        """The processor that gives a generated blank leaf its base version."""
        return self._processors.of_role(ProcessorRole.BLANK_LEAF)

    @property
    def prepared_keys(self) -> frozenset[str]:
        """The keys of the processors of base versions, whose pending versions a ``prepare-pages`` job writes.

        They are the ones a page is given when it is made. A version of another processor of the same stages, such as a
        half of a split spread, belongs to the run that makes it.
        """
        return frozenset({self.scan_copy.key, self.blank_leaf.key})

    def split_none(self, *, page: Page, scan: Scan, state: VersionState, moment: datetime) -> PageVersion:
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
        spec = self.scan_copy
        return PageVersion(
            id=VersionInputs(page_id=page.id, processor=spec.ref).identify(),
            page_id=page.id,
            stage=spec.stage,
            processor=spec.ref,
            data=size.as_data(),
            renditions=Renditions(ready=state is VersionState.READY, full=scan.renditions.full),
            state=state,
            created_at=moment,
        )

    def blank(
        self,
        *,
        page: Page,
        size: PageSize,
        full: Rendition,
        moment: datetime,
        paper_from: Collection[PageVersionId] | None = None,
    ) -> PageVersion:
        """Build the pending base version of a generated blank leaf, whose image a job writes.

        A white leaf has the size alone for its parameters, as a leaf had before it could be anything else. A leaf of
        the colour of the paper names the versions of the pages it takes the paper from, which is what makes a leaf made
        from other pages another version. The version of the processor is part of the identifier too, so a leaf made
        before the processor measured the paper differently is not found again when its choice is made again.

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
        spec = self.blank_leaf
        return PageVersion(
            id=VersionInputs(page_id=page.id, processor=spec.ref, params=params).identify(),
            page_id=page.id,
            stage=spec.stage,
            processor=spec.ref,
            params=params,
            data=size.as_data(),
            renditions=Renditions(ready=False, full=full),
            state=VersionState.PENDING,
            created_at=moment,
        )

    async def derive(self, key: Callable[[Rendition], StorageKey], *, assets: AssetStore, full: Rendition) -> None:
        """Cut the preview, the thumbnail and the tile pyramid from the ``full`` image stored under the keys.

        :param key: Function giving the storage key of each rendition of one scan or one page version.
        :type key: Callable[[Rendition], StorageKey]
        :param assets: Store of the derived files of the book the scan or the page version belongs to.
        :type assets: AssetStore
        :param full: Format the ``full`` image was written in, which names the file to cut from.
        :type full: Rendition
        """
        async with assets.readable(key(full)) as image:
            async with assets.writable(key(Rendition.PREVIEW)) as target:
                await self._tiler.preview(image, target)
            async with assets.writable(key(Rendition.THUMBNAIL)) as target:
                await self._tiler.thumbnail(image, target)
            tiles = key(Rendition.TILES)
            async with assets.writable(tiles) as target:
                await self._tiler.tile(image, target)
