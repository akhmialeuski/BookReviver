"""What a step of a recipe reads: the version before it, or the scan, and the image of it.

``StepSource`` is a value that is worked out once for the first step of a stage and moved on from step to step with
``after``. It holds the key of the image, the scale it is read on and the size of that image over the full one, so the
steps of a stage on a page, the split of a scan and the reading of the stage before it all take what they read from the
same class, and none of them imports another for it.
"""

from typing import TYPE_CHECKING, Self

from attrs import evolve, frozen

from bookreviver.domain.images import Rendition
from bookreviver.domain.keys import BookKeys
from bookreviver.domain.versions import PageSize, VersionScale

if TYPE_CHECKING:
    from bookreviver.domain.ids import PageVersionId, StorageKey
    from bookreviver.domain.metadata import MetadataMap
    from bookreviver.domain.sources import Scan
    from bookreviver.domain.versions import PageVersion


@frozen(kw_only=True)
class StepSource:
    """What a step reads: the version before it, or the scan, and the image of it.

    :ivar version_id: Version the step reads, or None when it reads a scan.
    :ivar image: Key of the image, the ``full`` or the ``preview`` of the version or the scan.
    :ivar data: ``data`` of the version, or the facts of the scan.
    :ivar scale: Whether the image is the full one or the preview, which is what the step reading it runs on.
    :ivar ratio: Size of the image over the size of the full image, 1 for a full run.
    :ivar book: What the book gives the step that reads the book for the fields its parameters leave to it, which lies
                under the settings of the page, or None for a run that has not worked it out.
    """

    version_id: PageVersionId | None
    image: StorageKey
    data: MetadataMap
    scale: VersionScale = VersionScale.FULL
    ratio: float = 1.0
    book: MetadataMap | None = None

    @staticmethod
    def preview_ratio(width_px: int, height_px: int, preview_long_side_px: int) -> float:
        """Work out the size of the preview of an image over its full size.

        :param width_px: Width of the full image.
        :type width_px: int
        :param height_px: Height of the full image.
        :type height_px: int
        :param preview_long_side_px: Longer side of a preview in pixels.
        :type preview_long_side_px: int
        :returns: The ratio, at most 1, since a preview is never larger than the image.
        :rtype: float
        """
        return min(1.0, preview_long_side_px / max(width_px, height_px))

    @classmethod
    def of_scan(cls, scan: Scan, scale: VersionScale, preview_long_side_px: int) -> Self:
        """Read a scan, whose image the page split reads.

        :param scan: The scan, whose renditions are ready.
        :type scan: Scan
        :param scale: Whether the full image or the preview is read.
        :type scale: VersionScale
        :param preview_long_side_px: Longer side of a preview in pixels.
        :type preview_long_side_px: int
        :returns: The source, with the facts of the scan for its data.
        :rtype: Self
        """
        facts = scan.facts
        if scale is VersionScale.PREVIEW:
            return cls(
                version_id=None,
                image=BookKeys.scan_rendition(scan, Rendition.PREVIEW),
                data=facts.as_data(),
                scale=scale,
                ratio=cls.preview_ratio(facts.width_px, facts.height_px, preview_long_side_px),
            )
        return cls(
            version_id=None,
            image=BookKeys.scan_rendition(scan, scan.renditions.full),
            data=facts.as_data(),
            scale=scale,
        )

    @classmethod
    def of_version(cls, version: PageVersion, scale: VersionScale, preview_long_side_px: int) -> Self:
        """Read a version of a page, which the first step of a stage reads.

        :param version: The version, whose images are ready.
        :type version: PageVersion
        :param scale: Whether the full image or the preview is read.
        :type scale: VersionScale
        :param preview_long_side_px: Longer side of a preview in pixels.
        :type preview_long_side_px: int
        :returns: The source, with the data of the version.
        :rtype: Self
        :raises ValueError: If the version has no image.
        """
        if scale is VersionScale.FULL:
            return cls(version_id=version.id, image=cls._image_of(version, scale), data=version.data, scale=scale)
        size = PageSize.from_data(version.data)
        ratio = 1.0 if size is None else cls.preview_ratio(size.width_px, size.height_px, preview_long_side_px)
        return cls(
            version_id=version.id, image=cls._image_of(version, scale), data=version.data, scale=scale, ratio=ratio
        )

    def after(self, version: PageVersion) -> Self:
        """Give what the next step of a chain reads once this source was made into a version.

        A preview made of a preview is as large of the full image as the preview it read, so the ratio goes on, and so
        does what the book gives.

        :param version: The version a step made of this source.
        :type version: PageVersion
        :returns: The source the next step reads, on the same scale.
        :rtype: Self
        :raises ValueError: If the version has no image.
        """
        return evolve(self, version_id=version.id, image=self._image_of(version, self.scale), data=version.data)

    @staticmethod
    def _image_of(version: PageVersion, scale: VersionScale) -> StorageKey:
        """Give the key of the image of a version a step reads on a scale.

        :param version: The version.
        :type version: PageVersion
        :param scale: Whether the full image or the preview is read.
        :type scale: VersionScale
        :returns: The key of the ``preview`` or of the ``full`` image.
        :rtype: StorageKey
        :raises ValueError: If the version has no image.
        """
        if version.renditions is None:
            err_msg = f'The version {version.id} has no image.'
            raise ValueError(err_msg)
        rendition = Rendition.PREVIEW if scale is VersionScale.PREVIEW else version.renditions.full
        return BookKeys.version_rendition(version, rendition)
