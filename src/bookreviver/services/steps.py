"""Running one processor on one input and storing what it made as the files of a page version.

The runner touches no database. It takes the processor, its checked parameters and the keys of its input files, lets the
asset store hand out local paths, runs the synchronous processor in a worker thread inside a work directory made next to
the files it will write, and stores each output with the rendition writer. The service that calls it owns the rows of
the versions, and decides which output is which version.

A processor makes its image without loss, or leaves one the runner can read as it is. The format of ``full`` is chosen
here from the image policy of the project and the colour of the output, so the processor knows nothing of that setting.
A version is written once into the directory of its identifier, which the asset store publishes whole or not at all,
so a step that fails leaves nothing behind and its version is made again under the same identifier.

A preview version holds only ``preview.jpg``. The IIIF pyramid is cut apart from the other files, for the versions
that are current and, on request, for any other, since it is the largest of the files and few versions are opened.
"""

import shutil
import tempfile
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
from asyncer import asyncify
from attrs import evolve, frozen

from bookreviver.domain.enums import (
    ColorMode,
    Rendition,
    ReviewReason,
    TransformKind,
    VersionData,
    VersionScale,
    VersionState,
)
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.geometry import Transform
from bookreviver.domain.values import COLOR_MODE_KEY, Renditions
from bookreviver.ports.processing import StepInput, StepOutput, StepResult

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from bookreviver.domain.entities import PageEdit, PageVersion
    from bookreviver.domain.enums import ImagePolicy, PageSide
    from bookreviver.domain.ids import StorageKey
    from bookreviver.domain.keys import ProjectKeys
    from bookreviver.domain.values import MetadataMap
    from bookreviver.ports.imaging import RenditionWriter, Tiler
    from bookreviver.ports.processing import ProcessorCatalog
    from bookreviver.ports.storage import AssetStore

NOT_READY: str = 'The version {version_id} has no image that is ready to cut.'


@frozen(kw_only=True)
class StepRun:
    """What one run of a processor reads.

    :ivar processor_key: Key of the processor to run.
    :ivar params: Parameters of the step after ``validate_params``.
    :ivar image: Key of the image the step reads, or None for a step that reads none.
    :ivar input_data: ``data`` of the input version, or the facts of the scan for a step that splits one.
    :ivar edit: Manual edit the step reads, or None.
    :ivar scale: Whether the step runs on the full image or on the preview.
    :ivar ratio: Size of the image the step reads over the size of the full image, 1 for a full run.
    :ivar side: Side of the book the page lies on, for a step that reads it, or None.
    :ivar references: Keys of the ``full`` images of other pages the step looks at without processing them, or none.
    :ivar skipped: Whether the page did not meet the condition of the step, so the processor is not run and the page
                   passes with its image and its data as they are.
    """

    processor_key: str
    params: MetadataMap
    input_data: MetadataMap
    image: StorageKey | None = None
    edit: PageEdit | None = None
    scale: VersionScale = VersionScale.FULL
    ratio: float = 1.0
    side: PageSide | None = None
    references: Sequence[StorageKey] = ()
    skipped: bool = False


class StepRunner:
    """Runs processors and stores their outputs as version files."""

    def __init__(
        self,
        *,
        assets: AssetStore,
        catalogue: ProcessorCatalog,
        renditions: RenditionWriter,
        tiler: Tiler,
        iiif_root: str,
    ) -> None:
        """Run processors of the catalogue, storing files through the asset store.

        :param assets: Store of the derived files, which hands out the local paths processors work on.
        :type assets: AssetStore
        :param catalogue: The processors the application can run.
        :type catalogue: ProcessorCatalog
        :param renditions: Writer of the ``full``, ``preview`` and ``thumb`` files of an output.
        :type renditions: RenditionWriter
        :param tiler: Port cutting the pyramid, and the preview of a preview run.
        :type tiler: Tiler
        :param iiif_root: Path the IIIF routes are mounted at, which a pyramid's ``info.json`` names as its address.
        :type iiif_root: str
        """
        self._assets = assets
        self._catalogue = catalogue
        self._renditions = renditions
        self._tiler = tiler
        self._iiif_root = iiif_root

    @asynccontextmanager
    async def execute(self, run: StepRun) -> AsyncIterator[StepResult]:
        """Run the processor in a worker thread while its input files and its work directory are open.

        The outputs name files in the work directory, which is removed when the context exits, so they are stored
        inside it. The work directory is made in the temporary directory of the machine, which ``TMPDIR`` moves to a
        disk with room for the largest page.

        :param run: What the step reads.
        :type run: StepRun
        :returns: Context manager yielding the result of the processor.
        :rtype: AsyncIterator[StepResult]
        :raises NotFoundError: If the processor, or a file it reads, is not stored.
        :raises DomainError: If the processor cannot run on this input.
        """
        processor = self._catalogue.get(run.processor_key)
        async with AsyncExitStack() as stack:
            image = None if run.image is None else await stack.enter_async_context(self._assets.readable(run.image))
            if run.skipped:
                yield self._unchanged(run, image)
                return
            mask = None
            if run.edit is not None and run.edit.mask_key is not None:
                mask = await stack.enter_async_context(self._assets.readable(run.edit.mask_key))
            references = [await stack.enter_async_context(self._assets.readable(key)) for key in run.references]
            workdir = stack.enter_context(tempfile.TemporaryDirectory())
            step_input = StepInput(
                image=image,
                scale=run.ratio,
                params=run.params,
                edit=run.edit,
                edit_mask=mask,
                input_data=run.input_data,
                side=run.side,
                references=references,
                workdir=Path(workdir),
            )
            work = processor.run if run.scale is VersionScale.FULL else processor.preview
            yield await asyncify(work)(step_input)

    @staticmethod
    def _unchanged(run: StepRun, image: Path | None) -> StepResult:
        """Make the result of a page that passes a step unchanged: its image, its data and its mark as they were.

        The data is the input's, so the steps after it read what the steps before found, and the mark the input carried
        stays while the step adds none. The result is the identity.

        :param run: What the step reads, whose input data is carried over.
        :type run: StepRun
        :param image: Local path of the image of the input.
        :type image: Path | None
        :returns: One output with the image of the input and the flag that the condition skipped the step.
        :rtype: StepResult
        """
        carried = run.input_data.get(VersionData.REVIEW)
        stated = run.input_data.get(COLOR_MODE_KEY)
        return StepResult(
            outputs=[
                StepOutput(
                    image=image,
                    color_mode=ColorMode(stated) if stated in set(ColorMode) else ColorMode.UNKNOWN,
                    data={**run.input_data, VersionData.SKIPPED: True, VersionData.SKIPPED_BY_CONDITION: True},
                    review=ReviewReason(carried) if carried in set(ReviewReason) else None,
                )
            ]
        )

    async def store(
        self, keys: ProjectKeys, version: PageVersion, output: StepOutput, *, policy: ImagePolicy, tiles: bool
    ) -> PageVersion:
        """Store the files of a version from one output of its step, and return the version as ready.

        Whatever an earlier attempt of the version left in its directory is removed first, since a stored file is
        never replaced, and a version that is stored again is one that never became ready.

        :param keys: Keys of the project owning the page.
        :type keys: ProjectKeys
        :param version: The version being made, whose identifier names its directory.
        :type version: PageVersion
        :param output: What the processor made.
        :type output: StepOutput
        :param policy: Image policy of the project, which with the colour of the output chooses the format of ``full``.
        :type policy: ImagePolicy
        :param tiles: Whether to cut the tile pyramid too, which a full run does for a current version only.
        :type tiles: bool
        :returns: The version with its transform, data, review mark, renditions and the state ready.
        :rtype: PageVersion
        """
        directory = keys.version_directory(version)
        await self._assets.delete_prefix(directory)
        renditions: Renditions | None = None
        async with self._assets.writable(directory) as target:
            if version.scale is VersionScale.PREVIEW:
                await anyio.Path(target).mkdir()
                if output.image is not None:
                    await self._tiler.preview(output.image, target / Rendition.PREVIEW)
                    renditions = Renditions(ready=True)
            elif output.image is None:
                await anyio.Path(target).mkdir()
            else:
                full = policy.full_format(output.color_mode)
                info = await self._renditions.write(output.image, target, full=full, color_mode=output.color_mode)
                renditions = Renditions(ready=True, full=info.full)
            if output.mask is not None:
                await asyncify(shutil.copyfile)(output.mask, target / Rendition.MASK)
            if output.mesh is not None:
                await asyncify(shutil.copyfile)(output.mesh, target / Rendition.MESH)
        transform = output.transform
        if output.mesh is not None:
            # The step cannot know where its mesh is stored, so the transform that names the file is made here
            transform = Transform(kind=TransformKind.MESH, mesh_key=keys.version_rendition(version, Rendition.MESH))
        stored = evolve(
            version,
            transform=transform,
            data=dict(output.data),
            review=output.review,
            renditions=renditions,
            state=VersionState.READY,
        )
        if tiles and version.scale is VersionScale.FULL and renditions is not None:
            return await self.cut_tiles(keys, stored)
        return stored

    async def discard(self, prefix: StorageKey) -> None:
        """Remove every file stored under a prefix, such as the directory of a page that was deleted.

        :param prefix: Storage key of the directory, which may hold nothing.
        :type prefix: StorageKey
        """
        await self._assets.delete_prefix(prefix)

    async def cut_tiles(self, keys: ProjectKeys, version: PageVersion) -> PageVersion:
        """Cut the IIIF tile pyramid of a ready version from its ``full`` image.

        :param keys: Keys of the project owning the page.
        :type keys: ProjectKeys
        :param version: A ready version of a full run that has an image.
        :type version: PageVersion
        :returns: The version with its pyramid marked as cut.
        :rtype: PageVersion
        :raises ConflictError: If the version has no ready image to cut.
        """
        if version.renditions is None or not version.renditions.ready or version.scale is not VersionScale.FULL:
            raise ConflictError(NOT_READY.format(version_id=version.id))
        tiles = keys.version_rendition(version, Rendition.TILES)
        await self._assets.delete_prefix(tiles)
        full = keys.version_rendition(version, version.renditions.full)
        async with self._assets.readable(full) as image, self._assets.writable(tiles) as target:
            await self._tiler.tile(image, target, resource_id=f'{self._iiif_root}/{tiles}')
        return evolve(version, tiles_ready=True)
