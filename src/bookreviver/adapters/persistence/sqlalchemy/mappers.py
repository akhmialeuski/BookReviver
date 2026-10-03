"""Mappers between table rows and frozen domain entities, one per aggregate.

A domain entity and a table row have different shapes, and neither can stand in for the other. The entity is a frozen
``attrs`` class with nested value objects: a :class:`~bookreviver.domain.entities.Project` holds a
:class:`~bookreviver.domain.values.BookDetails`, and a :class:`~bookreviver.domain.entities.PageVersion` a
:class:`~bookreviver.domain.values.ProcessorRef` and a :class:`~bookreviver.domain.geometry.Transform`. The row is a
flat, mutable class instrumented by SQLAlchemy, which assigns its attributes on load and tracks their changes. The
domain may not import SQLAlchemy, which import-linter enforces, so the translation lives here: ``to_row`` spreads the
value objects over columns, or into JSON where no query looks inside them, and ``to_entity`` gathers them back.

This is the Data Mapper pattern with explicit code. Imperative mapping of the domain classes is ruled out because the
ORM cannot assign attributes of a frozen class, and ``composite()`` is ruled out because it needs positional
constructors and a ``__composite_values__`` method on domain classes, which are keyword-only and must stay free of
storage concerns.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, override
from uuid import UUID

from attrs import asdict

from bookreviver.adapters.persistence.sqlalchemy.tables import (
    BookPlaceRow,
    JobRow,
    PageEditRow,
    PageRow,
    PageStageRow,
    PageVersionRow,
    ProjectRow,
    RecipeProfileRow,
    RecipeRow,
    RecipeRuleRow,
    ScanRow,
    SourceRow,
)
from bookreviver.domain.entities import (
    BookPlace,
    Job,
    Page,
    PageEdit,
    PageStage,
    PageVersion,
    Project,
    Recipe,
    RecipeProfile,
    RecipeRule,
    Scan,
    Source,
)
from bookreviver.domain.enums import ContributorRole, IdentifierScheme, RejectionReason, TransformKind
from bookreviver.domain.geometry import Point, Quad, Transform, geometry_from_data
from bookreviver.domain.ids import (
    AccountId,
    JobId,
    PageId,
    PageVersionId,
    ProjectId,
    RecipeId,
    RecipeProfileId,
    RecipeRuleId,
    ScanId,
    SourceId,
    StorageKey,
)
from bookreviver.domain.values import (
    BookDetails,
    BookIdentifier,
    CanvasPosition,
    Contributor,
    ImportRequest,
    ImportResult,
    MetadataSuggestion,
    ProcessorRef,
    Progress,
    RejectedFile,
    Renditions,
    ScanFacts,
    SourceFile,
    Step,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


def json_value(_owner: object, _field: object, value: object) -> object:
    """Turn a value of a stored value object into one JSON can hold, as ``attrs.asdict`` calls it for every value.

    :param _owner: Instance the value belongs to, which ``asdict`` passes and this rule does not need.
    :type _owner: object
    :param _field: Field of the instance that holds the value, likewise unused.
    :type _field: object
    :param value: The value to store.
    :type value: object
    :returns: The text of an identifier, which JSON has no type for, and any other value unchanged.
    :rtype: object
    """
    return str(value) if isinstance(value, UUID) else value


def contributors_to_json(contributors: Sequence[Contributor]) -> list[dict[str, str]]:
    """Turn contributors into the JSON list of ``{name, role}`` objects a description and a suggestion store.

    :param contributors: Contributors in title page order.
    :type contributors: Sequence[Contributor]
    :returns: One object per contributor, holding the code of the role and not the member.
    :rtype: list[dict[str, str]]
    """
    return [{'name': person.name, 'role': person.role.value} for person in contributors]


def contributors_from_json(stored: Sequence[Mapping[str, str]]) -> tuple[Contributor, ...]:
    """Build contributors from the JSON list ``contributors_to_json`` wrote.

    :param stored: Stored ``{name, role}`` objects.
    :type stored: Sequence[Mapping[str, str]]
    :returns: The contributors in the stored order.
    :rtype: tuple[Contributor, ...]
    """
    return tuple(Contributor(name=item['name'], role=ContributorRole(item['role'])) for item in stored)


def identifiers_to_json(identifiers: Sequence[BookIdentifier]) -> list[dict[str, str]]:
    """Turn identifiers into the JSON list of ``{scheme, value}`` objects a description and a suggestion store.

    :param identifiers: Identifiers in their order.
    :type identifiers: Sequence[BookIdentifier]
    :returns: One object per identifier, holding the value of the scheme and not the member.
    :rtype: list[dict[str, str]]
    """
    return [{'scheme': item.scheme.value, 'value': item.value} for item in identifiers]


def identifiers_from_json(stored: Sequence[Mapping[str, str]]) -> tuple[BookIdentifier, ...]:
    """Build identifiers from the JSON list ``identifiers_to_json`` wrote.

    :param stored: Stored ``{scheme, value}`` objects.
    :type stored: Sequence[Mapping[str, str]]
    :returns: The identifiers in the stored order.
    :rtype: tuple[BookIdentifier, ...]
    """
    return tuple(BookIdentifier(scheme=IdentifierScheme(item['scheme']), value=item['value']) for item in stored)


class RowMapper[EntityT, RowT](ABC):
    """Translation between one kind of domain entity and the row of its table.

    The generic repository of the adapter holds a mapper of this type, so one repository class serves every pair of
    entity and row.
    """

    @abstractmethod
    def to_entity(self, row: RowT) -> EntityT:
        """Build the domain entity stored in ``row``.

        :param row: Row loaded from the database.
        :type row: RowT
        :returns: Frozen entity holding every value of the row.
        :rtype: EntityT
        """

    @abstractmethod
    def to_row(self, entity: EntityT) -> RowT:
        """Build a new, unattached row holding every field of ``entity``.

        :param entity: Domain entity to store.
        :type entity: EntityT
        :returns: Transient row, ready to be added or merged into a session.
        :rtype: RowT
        """


class ProjectMapper(RowMapper[Project, ProjectRow]):
    """Translation of a project, whose description is flattened into project columns."""

    @override
    def to_entity(self, row: ProjectRow) -> Project:
        """Build the project stored in ``row``.

        :param row: Project row loaded from the database.
        :type row: ProjectRow
        :returns: Project with its description and settings.
        :rtype: Project
        """
        details = BookDetails(
            title=row.title,
            subtitle=row.subtitle,
            parallel_titles=tuple(row.parallel_titles),
            original_title=row.original_title,
            contributors=contributors_from_json(row.contributors),
            publisher=row.publisher,
            printer=row.printer,
            publication_place=row.publication_place,
            publication_year=row.publication_year,
            edition=row.edition,
            censorship=row.censorship,
            series=row.series,
            series_number=row.series_number,
            volume=row.volume,
            languages=tuple(row.languages),
            orthography=row.orthography,
            script=row.script,
            printed_pagination=row.printed_pagination,
            height_cm=row.height_cm,
            illustrations=row.illustrations,
            binding=row.binding,
            identifiers=identifiers_from_json(row.identifiers),
            subjects=tuple(row.subjects),
            rights=row.rights,
            copy_holder=row.copy_holder,
            copy_notes=row.copy_notes,
            notes=row.notes,
        )
        return Project(
            id=ProjectId(row.id),
            owner_id=AccountId(row.owner_id),
            details=details,
            image_policy=row.image_policy,
            cover_page_id=None if row.cover_page_id is None else PageId(row.cover_page_id),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @override
    def to_row(self, entity: Project) -> ProjectRow:
        """Build the row of ``entity``.

        :param entity: Project to store.
        :type entity: Project
        :returns: Transient project row.
        :rtype: ProjectRow
        """
        details = entity.details
        return ProjectRow(
            id=entity.id,
            owner_id=entity.owner_id,
            title=details.title,
            subtitle=details.subtitle,
            parallel_titles=list(details.parallel_titles),
            original_title=details.original_title,
            contributors=contributors_to_json(details.contributors),
            publisher=details.publisher,
            printer=details.printer,
            publication_place=details.publication_place,
            publication_year=details.publication_year,
            edition=details.edition,
            censorship=details.censorship,
            series=details.series,
            series_number=details.series_number,
            volume=details.volume,
            languages=list(details.languages),
            orthography=details.orthography,
            script=details.script,
            printed_pagination=details.printed_pagination,
            height_cm=details.height_cm,
            illustrations=details.illustrations,
            binding=details.binding,
            identifiers=identifiers_to_json(details.identifiers),
            subjects=list(details.subjects),
            rights=details.rights,
            copy_holder=details.copy_holder,
            copy_notes=details.copy_notes,
            notes=details.notes,
            image_policy=entity.image_policy,
            cover_page_id=entity.cover_page_id,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )


class PageMapper(RowMapper[Page, PageRow]):
    """Translation of a page of the book, whose fields are page columns one to one."""

    @override
    def to_entity(self, row: PageRow) -> Page:
        """Build the page stored in ``row``.

        :param row: Page row loaded from the database.
        :type row: PageRow
        :returns: Page with its order key, label, kind, origin and scan.
        :rtype: Page
        """
        return Page(
            id=PageId(row.id),
            project_id=ProjectId(row.project_id),
            order_key=row.order_key,
            label=row.label,
            kind=row.kind,
            origin=row.origin,
            scan_id=None if row.scan_id is None else ScanId(row.scan_id),
            slot=row.slot,
            included=row.included,
            notes=row.notes,
            group_label=row.group_label,
            created_at=row.created_at,
            updated_at=row.updated_at,
            revision=row.revision,
        )

    @override
    def to_row(self, entity: Page) -> PageRow:
        """Build the row of ``entity``.

        :param entity: Page to store.
        :type entity: Page
        :returns: Transient page row.
        :rtype: PageRow
        """
        return PageRow(
            id=entity.id,
            project_id=entity.project_id,
            order_key=entity.order_key,
            label=entity.label,
            kind=entity.kind,
            origin=entity.origin,
            scan_id=entity.scan_id,
            slot=entity.slot,
            included=entity.included,
            notes=entity.notes,
            group_label=entity.group_label,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            revision=entity.revision,
        )


class PageVersionMapper(RowMapper[PageVersion, PageVersionRow]):
    """Translation of a page version, whose processor is flattened into columns and whose transform is JSON."""

    @override
    def to_entity(self, row: PageVersionRow) -> PageVersion:
        """Build the page version stored in ``row``.

        :param row: Page version row loaded from the database.
        :type row: PageVersionRow
        :returns: Page version with its processor, transform, data and renditions state.
        :rtype: PageVersion
        """
        return PageVersion(
            id=PageVersionId(row.id),
            page_id=PageId(row.page_id),
            stage=row.stage,
            processor=ProcessorRef(key=row.processor_key, version=row.processor_version),
            input_id=None if row.input_id is None else PageVersionId(row.input_id),
            params=row.params,
            transform=self._transform(row.transform),
            data=row.data,
            review=row.review,
            renditions=(
                None
                if row.renditions_ready is None or row.renditions_full is None
                else Renditions(ready=row.renditions_ready, full=row.renditions_full)
            ),
            state=row.state,
            scale=row.scale,
            edit_hash=row.edit_hash,
            tiles_ready=row.tiles_ready,
            created_at=row.created_at,
            files_removed_at=row.files_removed_at,
        )

    @override
    def to_row(self, entity: PageVersion) -> PageVersionRow:
        """Build the row of ``entity``; the renditions keep their readiness and full format, never a later version.

        :param entity: Page version to store.
        :type entity: PageVersion
        :returns: Transient page version row.
        :rtype: PageVersionRow
        """
        return PageVersionRow(
            id=entity.id,
            page_id=entity.page_id,
            stage=entity.stage,
            processor_key=entity.processor.key,
            processor_version=entity.processor.version,
            input_id=entity.input_id,
            params=dict(entity.params),
            transform=asdict(entity.transform),
            data=dict(entity.data),
            review=entity.review,
            renditions_ready=None if entity.renditions is None else entity.renditions.ready,
            renditions_full=None if entity.renditions is None else entity.renditions.full,
            state=entity.state,
            scale=entity.scale,
            edit_hash=entity.edit_hash,
            tiles_ready=entity.tiles_ready,
            created_at=entity.created_at,
            files_removed_at=entity.files_removed_at,
        )

    @staticmethod
    def _transform(stored: Mapping[str, Any]) -> Transform:
        """Rebuild a transform from the JSON ``attrs.asdict`` made of it.

        :param stored: The transform as JSON, its quadrilateral a mapping of corners to points.
        :type stored: Mapping[str, Any]
        :returns: The transform with its kind and arguments.
        :rtype: Transform
        """
        quad = stored['quad']
        return Transform(
            kind=TransformKind(stored['kind']),
            quad=None if quad is None else Quad(**{corner: Point(**point) for corner, point in quad.items()}),
            angle=stored['angle'],
            mesh_key=None if stored['mesh_key'] is None else StorageKey(stored['mesh_key']),
            matrix=None if stored.get('matrix') is None else tuple(stored['matrix']),
        )


class SourceMapper(RowMapper[Source, SourceRow]):
    """Translation of a source, whose files and suggested description are stored as JSON."""

    @override
    def to_entity(self, row: SourceRow) -> Source:
        """Build the source stored in ``row``.

        :param row: Source row loaded from the database.
        :type row: SourceRow
        :returns: Source with its files, metadata and suggested description.
        :rtype: Source
        """
        return Source(
            id=SourceId(row.id),
            project_id=ProjectId(row.project_id),
            kind=row.kind,
            file_type=row.file_type,
            file_name=row.file_name,
            files=[SourceFile(**stored) for stored in row.files],
            size_bytes=row.size_bytes,
            sha256=row.sha256,
            scan_count=row.scan_count,
            metadata=row.metadata_,
            suggestion=MetadataSuggestion(
                title=row.suggestion['title'],
                contributors=contributors_from_json(row.suggestion['contributors']),
                publisher=row.suggestion['publisher'],
                publication_year=row.suggestion['publication_year'],
                languages=tuple(row.suggestion['languages']),
                identifiers=identifiers_from_json(row.suggestion['identifiers']),
                subjects=tuple(row.suggestion['subjects']),
            ),
            import_job_id=None if row.import_job_id is None else JobId(row.import_job_id),
            imported_at=row.imported_at,
        )

    @override
    def to_row(self, entity: Source) -> SourceRow:
        """Build the row of ``entity``.

        :param entity: Source to store.
        :type entity: Source
        :returns: Transient source row.
        :rtype: SourceRow
        """
        return SourceRow(
            id=entity.id,
            project_id=entity.project_id,
            kind=entity.kind,
            file_type=entity.file_type,
            file_name=entity.file_name,
            files=[asdict(stored) for stored in entity.files],
            size_bytes=entity.size_bytes,
            sha256=entity.sha256,
            scan_count=entity.scan_count,
            metadata_=dict(entity.metadata),
            suggestion={
                'title': entity.suggestion.title,
                'contributors': contributors_to_json(entity.suggestion.contributors),
                'publisher': entity.suggestion.publisher,
                'publication_year': entity.suggestion.publication_year,
                'languages': list(entity.suggestion.languages),
                'identifiers': identifiers_to_json(entity.suggestion.identifiers),
                'subjects': list(entity.suggestion.subjects),
            },
            import_job_id=entity.import_job_id,
            imported_at=entity.imported_at,
        )


class ScanMapper(RowMapper[Scan, ScanRow]):
    """Translation of a scan, whose facts and renditions state are flattened into scan columns."""

    @override
    def to_entity(self, row: ScanRow) -> Scan:
        """Build the scan stored in ``row``.

        :param row: Scan row loaded from the database.
        :type row: ScanRow
        :returns: Scan with its facts and renditions state.
        :rtype: Scan
        """
        facts = ScanFacts(
            width_px=row.width_px,
            height_px=row.height_px,
            color_mode=row.color_mode,
            dpi_x=row.dpi_x,
            dpi_y=row.dpi_y,
            bits_per_component=row.bits_per_component,
            image_format=row.image_format,
            width_mm=row.width_mm,
            height_mm=row.height_mm,
            has_text_layer=row.has_text_layer,
            extra=row.extra,
        )
        return Scan(
            id=ScanId(row.id),
            project_id=ProjectId(row.project_id),
            source_id=SourceId(row.source_id),
            number=row.number,
            source_label=row.source_label,
            facts=facts,
            renditions=Renditions(ready=row.renditions_ready, version=row.renditions_version, full=row.renditions_full),
        )

    @override
    def to_row(self, entity: Scan) -> ScanRow:
        """Build the row of ``entity``.

        :param entity: Scan to store.
        :type entity: Scan
        :returns: Transient scan row.
        :rtype: ScanRow
        """
        facts = entity.facts
        return ScanRow(
            id=entity.id,
            project_id=entity.project_id,
            source_id=entity.source_id,
            number=entity.number,
            source_label=entity.source_label,
            width_px=facts.width_px,
            height_px=facts.height_px,
            color_mode=facts.color_mode,
            dpi_x=facts.dpi_x,
            dpi_y=facts.dpi_y,
            bits_per_component=facts.bits_per_component,
            image_format=facts.image_format,
            width_mm=facts.width_mm,
            height_mm=facts.height_mm,
            has_text_layer=facts.has_text_layer,
            extra=dict(facts.extra),
            renditions_ready=entity.renditions.ready,
            renditions_version=entity.renditions.version,
            renditions_full=entity.renditions.full,
        )


class JobMapper(RowMapper[Job, JobRow]):
    """Translation of a job, whose progress is flattened into job columns and whose request and result are JSON."""

    @override
    def to_entity(self, row: JobRow) -> Job:
        """Build the job stored in ``row``.

        :param row: Job row loaded from the database.
        :type row: JobRow
        :returns: Job with its state, progress, request, result and timestamps.
        :rtype: Job
        """
        request, result = row.request, row.result
        return Job(
            id=JobId(row.id),
            project_id=ProjectId(row.project_id),
            kind=row.kind,
            state=row.state,
            progress=Progress(done=row.progress_done, total=row.progress_total),
            error=row.error,
            params=row.params,
            request=None if request is None else ImportRequest(files=[SourceFile(**file) for file in request['files']]),
            result=None
            if result is None
            else ImportResult(
                imported=[SourceId(UUID(source_id)) for source_id in result['imported']],
                rejected=[
                    RejectedFile(
                        file_name=rejected['file_name'],
                        reason=RejectionReason(rejected['reason']),
                        detail=rejected['detail'],
                    )
                    for rejected in result['rejected']
                ],
                skipped=result['skipped'],
            ),
            created_at=row.created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )

    @override
    def to_row(self, entity: Job) -> JobRow:
        """Build the row of ``entity``.

        :param entity: Job to store.
        :type entity: Job
        :returns: Transient job row.
        :rtype: JobRow
        """
        return JobRow(
            id=entity.id,
            project_id=entity.project_id,
            kind=entity.kind,
            state=entity.state,
            progress_done=entity.progress.done,
            progress_total=entity.progress.total,
            error=entity.error,
            params=dict(entity.params),
            request=None if entity.request is None else asdict(entity.request),
            # A source identifier is a UUID, which JSON has no type for
            result=None if entity.result is None else asdict(entity.result, value_serializer=json_value),
            created_at=entity.created_at,
            started_at=entity.started_at,
            finished_at=entity.finished_at,
        )


class PageStageMapper(RowMapper[PageStage, PageStageRow]):
    """Translation of the record of a stage of a page, whose columns are the fields of the entity."""

    @override
    def to_entity(self, row: PageStageRow) -> PageStage:
        """Build the record stored in ``row``.

        :param row: Page stage row loaded from the database.
        :type row: PageStageRow
        :returns: The record with its recipe, head version and state.
        :rtype: PageStage
        """
        return PageStage(
            page_id=PageId(row.page_id),
            stage=row.stage,
            recipe_id=None if row.recipe_id is None else RecipeId(row.recipe_id),
            head_version_id=None if row.head_version_id is None else PageVersionId(row.head_version_id),
            state=row.state,
            pinned=row.pinned,
            through_step=row.through_step,
            updated_at=row.updated_at,
        )

    @override
    def to_row(self, entity: PageStage) -> PageStageRow:
        """Build the row of ``entity``.

        :param entity: Record to store.
        :type entity: PageStage
        :returns: Transient page stage row.
        :rtype: PageStageRow
        """
        return PageStageRow(
            page_id=entity.page_id,
            stage=entity.stage,
            recipe_id=entity.recipe_id,
            head_version_id=entity.head_version_id,
            state=entity.state,
            pinned=entity.pinned,
            through_step=entity.through_step,
            updated_at=entity.updated_at,
        )


class PageEditMapper(RowMapper[PageEdit, PageEditRow]):
    """Translation of a manual edit, whose shape is stored as JSON data of its own kind."""

    @override
    def to_entity(self, row: PageEditRow) -> PageEdit:
        """Build the edit stored in ``row``.

        :param row: Page edit row loaded from the database.
        :type row: PageEditRow
        :returns: The edit with its shape rebuilt from its data.
        :rtype: PageEdit
        """
        return PageEdit(
            page_id=PageId(row.page_id),
            stage=row.stage,
            processor_key=row.processor_key,
            kind=row.kind,
            geometry=None if row.geometry is None else geometry_from_data(row.kind, row.geometry),
            mask_key=None if row.mask_key is None else StorageKey(row.mask_key),
            edit_hash=row.edit_hash,
            updated_at=row.updated_at,
        )

    @override
    def to_row(self, entity: PageEdit) -> PageEditRow:
        """Build the row of ``entity``.

        :param entity: Edit to store.
        :type entity: PageEdit
        :returns: Transient page edit row.
        :rtype: PageEditRow
        """
        return PageEditRow(
            page_id=entity.page_id,
            stage=entity.stage,
            processor_key=entity.processor_key,
            kind=entity.kind,
            geometry=None if entity.geometry is None else entity.geometry.to_data(),
            mask_key=entity.mask_key,
            edit_hash=entity.edit_hash,
            updated_at=entity.updated_at,
        )


class BookPlaceMapper(RowMapper[BookPlace, BookPlaceRow]):
    """Translation of the place of a book, whose canvas position is spread over three columns."""

    @override
    def to_entity(self, row: BookPlaceRow) -> BookPlace:
        """Build the place stored in ``row``.

        :param row: Book place row loaded from the database.
        :type row: BookPlaceRow
        :returns: The place with its canvas position, if the row has one.
        :rtype: BookPlace
        """
        zoom, centre_x, centre_y = row.canvas_zoom, row.canvas_centre_x, row.canvas_centre_y
        canvas = (
            None
            if zoom is None or centre_x is None or centre_y is None
            else CanvasPosition(zoom=zoom, centre_x=centre_x, centre_y=centre_y)
        )
        return BookPlace(
            account_id=AccountId(row.account_id),
            project_id=ProjectId(row.project_id),
            mode=row.mode,
            stage=row.stage,
            page_id=None if row.page_id is None else PageId(row.page_id),
            scan_id=None if row.scan_id is None else ScanId(row.scan_id),
            source_id=None if row.source_id is None else SourceId(row.source_id),
            view=row.view,
            compare=row.compare,
            filter=row.filter,
            canvas=canvas,
            strip_page_id=None if row.strip_page_id is None else PageId(row.strip_page_id),
            updated_at=row.updated_at,
        )

    @override
    def to_row(self, entity: BookPlace) -> BookPlaceRow:
        """Build the row of ``entity``.

        :param entity: Place to store.
        :type entity: BookPlace
        :returns: Transient book place row.
        :rtype: BookPlaceRow
        """
        canvas = entity.canvas
        return BookPlaceRow(
            account_id=entity.account_id,
            project_id=entity.project_id,
            mode=entity.mode,
            stage=entity.stage,
            page_id=entity.page_id,
            scan_id=entity.scan_id,
            source_id=entity.source_id,
            view=entity.view,
            compare=entity.compare,
            filter=entity.filter,
            canvas_zoom=None if canvas is None else canvas.zoom,
            canvas_centre_x=None if canvas is None else canvas.centre_x,
            canvas_centre_y=None if canvas is None else canvas.centre_y,
            strip_page_id=entity.strip_page_id,
            updated_at=entity.updated_at,
        )


class RecipeMapper(RowMapper[Recipe, RecipeRow]):
    """Translation of a recipe, whose steps are a JSON list."""

    @override
    def to_entity(self, row: RecipeRow) -> Recipe:
        """Build the recipe stored in ``row``.

        :param row: Recipe row loaded from the database.
        :type row: RecipeRow
        :returns: The recipe with its steps.
        :rtype: Recipe
        """
        return Recipe(
            id=RecipeId(row.id),
            project_id=ProjectId(row.project_id),
            stage=row.stage,
            name=row.name,
            steps=tuple(Step.from_map(step) for step in row.steps),
            active=row.active,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @override
    def to_row(self, entity: Recipe) -> RecipeRow:
        """Build the row of ``entity``.

        :param entity: Recipe to store.
        :type entity: Recipe
        :returns: Transient recipe row.
        :rtype: RecipeRow
        """
        return RecipeRow(
            id=entity.id,
            project_id=entity.project_id,
            stage=entity.stage,
            name=entity.name,
            steps=[step.to_map() for step in entity.steps],
            active=entity.active,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )


class RecipeProfileMapper(RowMapper[RecipeProfile, RecipeProfileRow]):
    """Translation of a recipe profile, whose steps are a JSON list."""

    @override
    def to_entity(self, row: RecipeProfileRow) -> RecipeProfile:
        """Build the profile stored in ``row``.

        :param row: Profile row loaded from the database.
        :type row: RecipeProfileRow
        :returns: The profile with its steps.
        :rtype: RecipeProfile
        """
        return RecipeProfile(
            id=RecipeProfileId(row.id),
            account_id=AccountId(row.account_id),
            stage=row.stage,
            name=row.name,
            steps=tuple(Step.from_map(step) for step in row.steps),
            is_default=row.is_default,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @override
    def to_row(self, entity: RecipeProfile) -> RecipeProfileRow:
        """Build the row of ``entity``.

        :param entity: Profile to store.
        :type entity: RecipeProfile
        :returns: Transient profile row.
        :rtype: RecipeProfileRow
        """
        return RecipeProfileRow(
            id=entity.id,
            account_id=entity.account_id,
            stage=entity.stage,
            name=entity.name,
            steps=[step.to_map() for step in entity.steps],
            is_default=entity.is_default,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )


class RecipeRuleMapper(RowMapper[RecipeRule, RecipeRuleRow]):
    """Translation of a rule of a stage, whose columns are the fields of the entity."""

    @override
    def to_entity(self, row: RecipeRuleRow) -> RecipeRule:
        """Build the rule stored in ``row``.

        :param row: Rule row loaded from the database.
        :type row: RecipeRuleRow
        :returns: The rule with its condition and recipe.
        :rtype: RecipeRule
        """
        return RecipeRule(
            id=RecipeRuleId(row.id),
            project_id=ProjectId(row.project_id),
            stage=row.stage,
            condition=row.condition,
            group_label=row.group_label,
            recipe_id=RecipeId(row.recipe_id),
            order=row.order,
        )

    @override
    def to_row(self, entity: RecipeRule) -> RecipeRuleRow:
        """Build the row of ``entity``.

        :param entity: Rule to store.
        :type entity: RecipeRule
        :returns: Transient rule row.
        :rtype: RecipeRuleRow
        """
        return RecipeRuleRow(
            id=entity.id,
            project_id=entity.project_id,
            stage=entity.stage,
            condition=entity.condition,
            group_label=entity.group_label,
            recipe_id=entity.recipe_id,
            order=entity.order,
        )
