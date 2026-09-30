"""Schemas of the sources a book was assembled from and of their scans.

A source is an uploaded file kept as it was uploaded, and a scan is one image inside it. Both are read-only records of
where the book came from. The technical metadata of a source is a dictionary because its keys depend on the format of
the file, while the description values a source suggests have fixed fields. A scan shows its images once they are cut,
as paths of the IIIF route without scheme or host, and none before.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Self

from fastapi import Query
from fastapi_pagination import Params

from bookreviver.api.schemas.base import ResponseModel
from bookreviver.api.schemas.images import ImagePathsSchema
from bookreviver.domain.enums import ColorMode, FileType, SourceKind
from bookreviver.domain.ids import JobId, ScanId, SourceId
from bookreviver.domain.keys import ProjectKeys

if TYPE_CHECKING:
    from starlette.requests import Request

    from bookreviver.domain.entities import Scan


class ScanQuery(Params):
    """The query of a list of scans: the page parameters of every list, and the source to list the scans of.

    :ivar page: Number of the page of the list, from one.
    :ivar size: Number of scans in one page.
    :ivar source_id: Source whose scans to list, or omitted for the scans of every source of the project.
    """

    source_id: SourceId | None = Query(None, description='List only the scans of this source')


class SourceFileSchema(ResponseModel):
    """One stored file of a source.

    :ivar name: Name of the file inside the source's directory.
    :ivar size_bytes: Size of the file in bytes.
    :ivar sha256: SHA-256 digest of the file's content as lower-case hexadecimal digits.
    """

    name: str
    size_bytes: int
    sha256: str


class MetadataSuggestionSchema(ResponseModel):
    """Description fields found in a source; an empty string means nothing was found.

    :ivar title: Title found in the source.
    :ivar authors: Authors found in the source.
    :ivar publisher: Publisher found in the source.
    :ivar publication_year: Year of publication found in the source.
    :ivar language: Language found in the source.
    """

    title: str
    authors: str
    publisher: str
    publication_year: str
    language: str


class SourceSchema(ResponseModel):
    """An uploaded file of a book, or the files of one indirect DjVu document, never changed after its import.

    :ivar id: Identifier of the source.
    :ivar kind: Kind of the source, which selects the format that reads it.
    :ivar file_type: Exact format of the main file.
    :ivar file_name: Name the browser sent for the main file.
    :ivar files: Stored files, the main file first; more than one only for an indirect DjVu document.
    :ivar size_bytes: Total size of the files in bytes.
    :ivar sha256: SHA-256 digest of the main file.
    :ivar scan_count: Number of scans the source holds.
    :ivar metadata: Technical metadata of the format, whose keys depend on the kind of source.
    :ivar suggestion: Values of the book description found in the file.
    :ivar import_job_id: Import job that created the source, or None once that job is deleted.
    :ivar imported_at: When the source became part of the project.
    """

    id: SourceId
    kind: SourceKind
    file_type: FileType
    file_name: str
    files: list[SourceFileSchema]
    size_bytes: int
    sha256: str
    scan_count: int
    metadata: dict[str, Any]
    suggestion: MetadataSuggestionSchema
    import_job_id: JobId | None
    imported_at: datetime


class ScanFactsSchema(ResponseModel):
    """Technical facts of one scan.

    :ivar width_px: Width of the image in pixels.
    :ivar height_px: Height of the image in pixels.
    :ivar color_mode: Whether the image is bilevel, gray or colour.
    :ivar dpi_x: Horizontal resolution in dots per inch, or None when the source does not record it.
    :ivar dpi_y: Vertical resolution in dots per inch, or None when the source does not record it.
    :ivar bits_per_component: Bit depth of one colour component, or None when unknown.
    :ivar image_format: Human name of the image format, such as ``JPEG`` or ``TIFF``.
    :ivar width_mm: Physical width in millimetres, or None without a resolution.
    :ivar height_mm: Physical height in millimetres, or None without a resolution.
    :ivar has_text_layer: Whether the scan carries text, such as the OCR layer of a scanned PDF page.
    :ivar extra: Further facts under the keys of the inspector that reported them.
    """

    width_px: int
    height_px: int
    color_mode: ColorMode
    dpi_x: float | None
    dpi_y: float | None
    bits_per_component: int | None
    image_format: str
    width_mm: float | None
    height_mm: float | None
    has_text_layer: bool
    extra: dict[str, Any]


class ScanSchema(ResponseModel):
    """One image of a source, as the file holds it.

    :ivar id: Identifier of the scan.
    :ivar source_id: Source holding the scan.
    :ivar number: Position of the scan in its source, starting at 0.
    :ivar source_label: Page label the file itself gives, such as the PDF page label ``xii``, or empty.
    :ivar facts: Technical facts of the image.
    :ivar images: Paths of the derived images of the scan, or None until they are cut.
    """

    id: ScanId
    source_id: SourceId
    number: int
    source_label: str
    facts: ScanFactsSchema
    images: ImagePathsSchema | None

    @classmethod
    def from_scan(cls, scan: Scan, request: Request) -> Self:
        """Build the schema of a scan with the paths of its images once they are ready.

        :param scan: The scan.
        :type scan: Scan
        :param request: The request, whose application knows the route that serves the images.
        :type request: Request
        :returns: The scan resource, with images once its renditions are ready.
        :rtype: Self
        """
        keys = ProjectKeys(scan.project_id)
        images = None
        if scan.renditions.ready:
            images = ImagePathsSchema.of(request, lambda rendition: keys.scan_rendition(scan, rendition))
        return cls(
            id=scan.id,
            source_id=scan.source_id,
            number=scan.number,
            source_label=scan.source_label,
            facts=ScanFactsSchema.model_validate(scan.facts),
            images=images,
        )
