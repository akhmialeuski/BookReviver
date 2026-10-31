"""The images of a book: their colour, how a book stores them, and the files derived from a scan or result."""

from typing import ClassVar

from attrs import field, frozen, validators

from bookreviver.domain.labels import LabeledStrEnum


class ColorMode(LabeledStrEnum):
    """Colour depth of a page image as stored in the source."""

    BILEVEL = 'bilevel', 'Black and white'
    GRAY = 'gray', 'Grayscale'
    COLOR = 'color', 'Colour'
    UNKNOWN = 'unknown', 'Unknown'


class ImagePolicy(LabeledStrEnum):
    """How a book stores the ``full`` image of its scans and page versions.

    A bilevel image is a lossless PNG under either policy. The policy decides only gray and colour images, and a change
    applies to images written afterwards, so nothing stored is encoded again.
    """

    COMPACT = 'compact', 'Compact: gray and colour images as JPEG'
    LOSSLESS = 'lossless', 'Lossless: every image as PNG'

    def full_format(self, color_mode: ColorMode) -> Rendition:
        """Return the format of the ``full`` image of a page of ``color_mode``, by decision 22 of the book model.

        A bilevel page is a 1-bit PNG whatever the policy, since JPEG rings around strokes and a 1-bit PNG is smaller.
        Any other page, one of unknown colour included, is a JPEG under ``compact`` and a PNG under ``lossless``.

        :param color_mode: Colour mode of the page, as its scan reports it.
        :type color_mode: ColorMode
        :returns: ``Rendition.FULL_PNG`` or ``Rendition.FULL_JPEG``.
        :rtype: Rendition
        """
        if color_mode is ColorMode.BILEVEL or self is ImagePolicy.LOSSLESS:
            return Rendition.FULL_PNG
        return Rendition.FULL_JPEG


class Rendition(LabeledStrEnum):
    """One of the derived files of a scan or a page version, named by its value inside their directory.

    ``full`` is a JPEG or a PNG as the book's ``ImagePolicy`` and the colour of the image decide, so it has one
    member per format. ``preview`` and ``thumb`` are always JPEG.
    """

    FULL_JPEG = 'full.jpg', 'Native resolution image as JPEG'
    FULL_PNG = 'full.png', 'Native resolution image as PNG'
    PREVIEW = 'preview.jpg', 'Preview, 2048 px on the longer side'
    THUMBNAIL = 'thumb.jpg', 'Thumbnail'
    TILES = 'iiif', 'IIIF tile pyramid'
    MASK = 'mask.png', 'Mask of the areas a step removed'
    MESH = 'mesh.json', 'Grid of the mesh a dewarping followed'


@frozen(kw_only=True)
class Renditions:
    """State of the derived files of a scan or a page version: full image, preview, thumbnail and tile pyramid.

    A scan whose files are cut again gets the next version, so the keys of the new files differ from the old ones and
    no cache serves a stale image. A page version is written once into the directory of its own identifier, so its
    renditions stay at the first version.

    The format of ``full`` is chosen when the image is written, from its colour and the book's ``ImagePolicy``, and
    kept here. It is not computed from the policy when read, because a later change of the policy must not change the
    storage key of an image already stored. A page version copies the format of the scan it is cut from.

    :ivar ready: Whether every derived file of the current version is published.
    :ivar version: Version of the derived files, part of their storage keys.
    :ivar full: Format of the ``full`` image, ``Rendition.FULL_JPEG`` or ``Rendition.FULL_PNG``.
    """

    FIRST_VERSION: ClassVar[int] = 1
    FULL_FORMATS: ClassVar[frozenset[Rendition]] = frozenset({Rendition.FULL_JPEG, Rendition.FULL_PNG})

    ready: bool = False
    version: int = field(default=FIRST_VERSION, validator=validators.ge(FIRST_VERSION))
    full: Rendition = field(default=Rendition.FULL_JPEG, validator=validators.in_(FULL_FORMATS))


@frozen(kw_only=True)
class RenditionInfo:
    """What a writer of renditions made of an image: its size and the format of its ``full`` file.

    :ivar width_px: Width of the image in pixels.
    :ivar height_px: Height of the image in pixels.
    :ivar full: Format the ``full`` image was written in.
    """

    width_px: int = field(validator=validators.gt(0))
    height_px: int = field(validator=validators.gt(0))
    full: Rendition = field(validator=validators.in_(Renditions.FULL_FORMATS))
