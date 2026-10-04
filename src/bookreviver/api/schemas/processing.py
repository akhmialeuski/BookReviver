"""Schemas of the processing framework: processors, recipes, runs and previews, stages of a page and page versions.

A processor tells the interface its parameters as a JSON Schema, from which the interface builds the form, so the
schema of a processor is a free-form object here. A recipe is the steps of one stage, and the body of one is the same
for the active recipe and a variant. A run and a preview start a job and answer with it, so their bodies only say what
to run. A page version carries its provenance, its transform and, once its files are written, the paths of its images,
which are those of the IIIF route as for every other image; a preview has only the path of its preview image.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, Self

from attrs import evolve
from fastapi import Query
from fastapi_pagination import Params
from pydantic import Field, model_validator

from bookreviver.api.route_names import RouteName
from bookreviver.api.schemas.base import RequestModel, ResponseModel
from bookreviver.api.schemas.images import ImagePathsSchema
from bookreviver.api.schemas.types import (
    RECIPE_STEPS_MAX_LENGTH,
    PageIdList,
    ProcessorKeyText,
    RecipeName,
    VersionIdentifier,
)
from bookreviver.domain.enums import (
    AppliesTo,
    EditorKind,
    OrderMode,
    OrderRuleKind,
    ProcessorScope,
    Rendition,
    ReviewReason,
    Stage,
    StageState,
    TransformKind,
    VersionData,
    VersionOutput,
    VersionScale,
    VersionState,
    WorkerPool,
)
from bookreviver.domain.ids import PageId, PageVersionId, ProjectId, RecipeId, RecipeProfileId, StepId
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import PIN_NEEDS_RECIPE, RecipeDraft, StageRun, Step, StepPreview, VersionFilter

if TYPE_CHECKING:
    from collections.abc import Sequence

    from starlette.requests import Request

    from bookreviver.domain.entities import PageStage, PageVersion, Recipe
    from bookreviver.domain.values import OrderIssue

STEP_INDEX_OUT_OF_RANGE: str = 'The step index must name one of the steps.'


class OrderRuleSchema(ResponseModel):
    """The place a processor asks for relative to the steps of another processor.

    :ivar processor_key: Key of the other processor.
    :ivar reason: One sentence that says why the place matters.
    """

    processor_key: str
    reason: str


class ProcessorSchema(ResponseModel):
    """A processor a recipe can use.

    :ivar key: Key of the processor, such as ``geometry.deskew``.
    :ivar version: Version of its algorithm.
    :ivar title: Name the interface shows.
    :ivar stage: Stage whose recipe it can be put into.
    :ivar scope: Whether it makes one output for a page or one for each part of a scan.
    :ivar outputs: What it writes.
    :ivar parameters: JSON Schema of its parameters, from which the interface builds the form.
    :ivar editor: Editor of the manual edit it reads.
    :ivar pool: Class of worker it runs on.
    :ivar after: Processors whose steps its step usually stands after.
    :ivar before: Processors whose steps its step usually stands before.
    :ivar requires_after: Processors whose steps its step must stand after, which the interface keeps it from leaving.
    """

    key: str
    version: str
    title: str
    stage: Stage
    scope: ProcessorScope
    outputs: list[VersionOutput]
    parameters: dict[str, Any]
    editor: EditorKind
    pool: WorkerPool
    after: list[OrderRuleSchema]
    before: list[OrderRuleSchema]
    requires_after: list[OrderRuleSchema]


class StepSchema(ResponseModel):
    """One step of a recipe: a processor and the parameters it runs with.

    :ivar processor_key: Key of the processor.
    :ivar params: Parameters of the step, with the defaults of the processor filled in.
    :ivar enabled: Whether a run and a preview run the step; a step that is off keeps its parameters.
    :ivar step_id: Identifier of the step, which stays as the step is moved and saved and which its manual edits name.
    :ivar applies_to: Which pages the step processes; the others pass it unchanged.
    """

    processor_key: str
    params: dict[str, Any]
    enabled: bool
    step_id: StepId
    applies_to: AppliesTo


class OrderIssueSchema(ResponseModel):
    """A step that stands where its processor does not want it.

    :ivar step_id: The step that is out of place.
    :ivar processor_key: Key of its processor.
    :ivar kind: Whether the place is the usual one, which the step may leave, or a required one.
    :ivar other_step_id: The step it is compared with.
    :ivar other_key: Key of the processor of that step.
    :ivar reason: One sentence that says why the place matters.
    """

    step_id: StepId
    processor_key: str
    kind: OrderRuleKind
    other_step_id: StepId
    other_key: str
    reason: str


class RecipeSchema(ResponseModel):
    """A recipe: the ordered steps of one stage, active or a variant.

    :ivar id: Identifier of the recipe.
    :ivar project_id: Project owning the recipe.
    :ivar stage: Stage the recipe processes.
    :ivar name: Name the user sees.
    :ivar steps: The steps in the order they run.
    :ivar active: Whether the recipe is the one the stage runs by default.
    :ivar profile_id: The profile of the account the recipe was made from, or null for a recipe that was not.
    :ivar created_at: When the recipe was created.
    :ivar updated_at: When the recipe was last changed.
    :ivar order_issues: The steps that stand off the place their processors ask for, none for a recipe in its usual
                        order. A required place appears here only for a recipe saved in the free order.
    """

    id: RecipeId
    project_id: ProjectId
    stage: Stage
    name: str
    steps: list[StepSchema]
    active: bool
    profile_id: RecipeProfileId | None
    created_at: datetime
    updated_at: datetime
    order_issues: list[OrderIssueSchema] = Field(default_factory=list)

    @classmethod
    def of(cls, recipe: Recipe, issues: Sequence[OrderIssue]) -> Self:
        """Build the schema of a recipe with the order issues its steps have.

        :param recipe: The recipe.
        :type recipe: Recipe
        :param issues: The steps of the recipe that stand off the place their processors ask for.
        :type issues: Sequence[OrderIssue]
        :returns: The schema.
        :rtype: Self
        """
        return cls.model_validate(recipe).model_copy(
            update={'order_issues': [OrderIssueSchema.model_validate(issue) for issue in issues]}
        )


class StepBody(RequestModel):
    """A step of a recipe or of a preview as the interface sends it.

    :ivar processor_key: Key of the processor.
    :ivar params: Parameters of the step, which the processor checks and fills in.
    :ivar enabled: Whether a run and a preview run the step, on unless the interface switches it off.
    :ivar step_id: Identifier of a step that already exists, which the interface sends back to keep its edits, or
                   omitted for a step that is added, which gets a new one.
    :ivar applies_to: Which pages the step processes, all of them unless the interface says otherwise.
    """

    processor_key: ProcessorKeyText
    params: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    step_id: StepId | None = None
    applies_to: AppliesTo = AppliesTo.ALL

    def to_step(self) -> Step:
        """Return the step as the domain states it.

        :returns: The step, with the identifier it came with or a new one.
        :rtype: Step
        """
        step = Step(
            processor_key=self.processor_key, params=self.params, enabled=self.enabled, applies_to=self.applies_to
        )
        return step if self.step_id is None else evolve(step, step_id=self.step_id)


class RecipeBody(RequestModel):
    """The name and the steps of a recipe, for the active recipe and for a variant alike.

    :ivar name: Name of the recipe.
    :ivar steps: Its steps in the order they run, each checked against its processor.
    :ivar order: ``usual`` refuses a step that stands where it cannot work, with the reason as the detail of a 422, and
                 ``free`` saves it and reports it in the answer. A step off its usual place is saved either way.
    """

    name: RecipeName
    steps: Annotated[list[StepBody], Field(min_length=1, max_length=RECIPE_STEPS_MAX_LENGTH)]
    order: OrderMode = OrderMode.USUAL

    def to_draft(self) -> RecipeDraft:
        """Return the name, the steps and the order as the domain states them.

        :returns: The draft.
        :rtype: RecipeDraft
        """
        return RecipeDraft(name=self.name, steps=[step.to_step() for step in self.steps], order=self.order)


class StageRunBody(RequestModel):
    """What a run of a stage is asked to do; the stage is in the address.

    :ivar recipe_id: Recipe to run it by, or omitted for the active recipe.
    :ivar page_ids: Pages to run it on, or omitted for every page with an image.
    :ivar confirm_unsplit: Confirmation that undoing a page split deletes the right half of a spread, without which a
                           run that would do so leaves that page failed.
    :ivar pin: Whether to pin the recipe to the pages of the run, so a later run without a recipe keeps it there. It is
               given with a recipe, since a run that chooses the recipes pins nothing.
    :ivar through_step: Index in the recipe of the last step to run, from zero, or omitted to run through the last step
                        that is on. The steps before it come from the cache of versions when their inputs did not
                        change.
    """

    recipe_id: RecipeId | None = None
    page_ids: PageIdList | None = None
    confirm_unsplit: bool = False
    pin: bool = False
    through_step: Annotated[int, Field(ge=0, lt=RECIPE_STEPS_MAX_LENGTH)] | None = None

    @model_validator(mode='after')
    def _pin_names_a_recipe(self) -> Self:
        """Check that a run that pins also names the recipe to pin.

        :returns: The body unchanged.
        :rtype: Self
        :raises ValueError: If the run pins and names no recipe.
        """
        if self.pin and self.recipe_id is None:
            raise ValueError(PIN_NEEDS_RECIPE)
        return self

    def to_run(self, stage: Stage) -> StageRun:
        """Return the run as the domain states it.

        :param stage: Stage in the address of the request.
        :type stage: Stage
        :returns: The run.
        :rtype: StageRun
        """
        return StageRun(
            stage=stage,
            recipe_id=self.recipe_id,
            page_ids=None if self.page_ids is None else tuple(self.page_ids),
            confirm_unsplit=self.confirm_unsplit,
            pin=self.pin,
            through_step=self.through_step,
        )


class StepPreviewBody(RequestModel):
    """The steps of a form to preview on one page, up to one of them; the stage is in the address.

    :ivar page_id: Page to preview on.
    :ivar steps: The steps as the form has them.
    :ivar step_index: Index of the last step whose result is wanted.
    """

    page_id: PageId
    steps: Annotated[list[StepBody], Field(min_length=1, max_length=RECIPE_STEPS_MAX_LENGTH)]
    step_index: Annotated[int, Field(ge=0)]

    @model_validator(mode='after')
    def _index_names_a_step(self) -> Self:
        """Check that the index lies within the steps.

        :returns: The body unchanged.
        :rtype: Self
        :raises ValueError: If the index is past the last step.
        """
        if self.step_index >= len(self.steps):
            raise ValueError(STEP_INDEX_OUT_OF_RANGE)
        return self

    def to_preview(self, stage: Stage) -> StepPreview:
        """Return the preview as the domain states it.

        :param stage: Stage in the address of the request.
        :type stage: Stage
        :returns: The preview.
        :rtype: StepPreview
        """
        return StepPreview(
            page_id=self.page_id,
            stage=stage,
            steps=tuple(step.to_step() for step in self.steps),
            step_index=self.step_index,
        )


class PageStageSchema(ResponseModel):
    """The current version of a stage of a page and whether it is up to date; the data of a stage event too.

    :ivar page_id: Page the record belongs to.
    :ivar stage: The stage.
    :ivar recipe_id: Recipe the page was processed by, or None.
    :ivar head_version_id: The current version of the stage, or None.
    :ivar state: Whether the current version matches the inputs of the stage.
    :ivar pinned: Whether the recipe is pinned to the page, so a run without a recipe keeps it.
    :ivar through_step: Index in the recipe of the last step the page was run through when that is before the last step
                        that is on, so the page is not ready for the next stage, or None.
    :ivar updated_at: When the record last changed.
    """

    page_id: PageId
    stage: Stage
    recipe_id: RecipeId | None
    head_version_id: PageVersionId | None
    state: StageState
    pinned: bool
    through_step: int | None
    updated_at: datetime

    @classmethod
    def of(cls, record: PageStage) -> Self:
        """Build the schema of a stage record.

        :param record: The record.
        :type record: PageStage
        :returns: The schema.
        :rtype: Self
        """
        return cls.model_validate(record)


class HeadChoice(RequestModel):
    """The version to make the current one of a stage.

    :ivar version_id: Identifier of the version, which must be ready and made by a full run of this page and stage.
    """

    version_id: VersionIdentifier


class VersionQuery(Params):
    """The query of the list of versions of a page: the page parameters, and the stage and the scale to list.

    :ivar page: Number of the page of the list, from one.
    :ivar size: Number of versions in one page of the list.
    :ivar stage: Stage whose versions are listed, or omitted for every stage.
    :ivar scale: Whether to list full runs or previews, or omitted for both.
    """

    stage: Stage | None = Query(default=None, description='List the versions of this stage only')
    scale: VersionScale | None = Query(default=None, description='List full runs or previews only')

    def to_filter(self) -> VersionFilter:
        """Return the filter as the domain states it.

        :returns: The stage and the scale.
        :rtype: VersionFilter
        """
        return VersionFilter(stage=self.stage, scale=self.scale)


class PointSchema(ResponseModel):
    """A point in the pixels of an image.

    :ivar x: Distance from the left edge.
    :ivar y: Distance from the top edge.
    """

    x: float
    y: float


class QuadSchema(ResponseModel):
    """A quadrilateral in the pixels of an image.

    :ivar top_left: Corner at the top left.
    :ivar top_right: Corner at the top right.
    :ivar bottom_right: Corner at the bottom right.
    :ivar bottom_left: Corner at the bottom left.
    """

    top_left: PointSchema
    top_right: PointSchema
    bottom_right: PointSchema
    bottom_left: PointSchema


class TransformSchema(ResponseModel):
    """How a step moves the coordinates of its input image to its output image.

    :ivar kind: Kind of the transform.
    :ivar quad: Area of the input that becomes the output, for a crop or a perspective correction.
    :ivar angle: Angle of a rotation in degrees, counter-clockwise.
    :ivar mesh_key: Storage key of the mesh a dewarping follows.
    :ivar matrix: The nine numbers of the 3 by 3 matrix from the input to the output, in rows.
    """

    kind: TransformKind
    quad: QuadSchema | None
    angle: float | None
    mesh_key: str | None
    matrix: list[float] | None


class ProcessorRefSchema(ResponseModel):
    """The processor that made a version.

    :ivar key: Key of the processor.
    :ivar version: Version of its algorithm.
    """

    key: str
    version: str


class PageVersionSchema(ResponseModel):
    """The result of one processing step on one page.

    :ivar id: Identifier of the version, the hash of what produced it.
    :ivar page_id: Page the version belongs to.
    :ivar stage: Stage of the step.
    :ivar processor: Processor that ran the step.
    :ivar input_id: Version the step read, or None for a base version.
    :ivar params: Parameters of the step.
    :ivar transform: Transform of coordinates from the input.
    :ivar data: Data the step found, such as an angle or a confidence.
    :ivar review: Why the step asks for a second look at the page though it finished, or None when it was sure.
    :ivar state: Where the version is in its life cycle.
    :ivar scale: Whether the step ran on the full image or on a preview.
    :ivar edit_hash: Hash of the manual edit the step read, or empty.
    :ivar tiles_ready: Whether the tile pyramid is cut, which a client asks for when it is not.
    :ivar error: Why the version failed, or empty.
    :ivar images: Paths of the images of a ready full run that has an image, or None.
    :ivar preview: Path of the preview image of a preview run, or None.
    :ivar created_at: When the version was created.
    :ivar files_removed: Whether a collection removed the files of the version, so it has no image until a run makes it
                         again.
    :ivar files_removed_at: When the files were removed, or None while the version has them.
    """

    id: PageVersionId
    page_id: PageId
    stage: Stage
    processor: ProcessorRefSchema
    input_id: PageVersionId | None
    params: dict[str, Any]
    transform: TransformSchema
    data: dict[str, Any]
    review: ReviewReason | None
    state: VersionState
    scale: VersionScale
    edit_hash: str
    tiles_ready: bool
    error: str
    images: ImagePathsSchema | None
    preview: str | None
    created_at: datetime
    files_removed: bool
    files_removed_at: datetime | None

    @classmethod
    def of(cls, version: PageVersion, project_id: ProjectId, request: Request) -> Self:
        """Build the schema of a version, with the paths of its images once they are written.

        :param version: The version.
        :type version: PageVersion
        :param project_id: Project owning the page, whose keys place the files.
        :type project_id: ProjectId
        :param request: The request, whose application knows the route that serves the images.
        :type request: Request
        :returns: The schema.
        :rtype: Self
        """
        keys = ProjectKeys(project_id)
        ready = version.renditions is not None and version.renditions.ready
        images = None
        preview = None
        if version.renditions is not None and ready and version.scale is VersionScale.FULL:
            images = ImagePathsSchema.of(
                request, lambda rendition: keys.version_rendition(version, rendition), full=version.renditions.full
            )
        elif ready:
            preview = str(
                request.app.url_path_for(RouteName.IIIF_FILE, key=keys.version_rendition(version, Rendition.PREVIEW))
            )
        return cls(
            id=version.id,
            page_id=version.page_id,
            stage=version.stage,
            processor=ProcessorRefSchema.model_validate(version.processor),
            input_id=version.input_id,
            params=dict(version.params),
            transform=TransformSchema.model_validate(version.transform),
            data=dict(version.data),
            review=version.review,
            state=version.state,
            scale=version.scale,
            edit_hash=version.edit_hash,
            tiles_ready=version.tiles_ready,
            error=str(version.data.get(VersionData.ERROR, '')),
            images=images,
            preview=preview,
            created_at=version.created_at,
            files_removed=version.files_removed,
            files_removed_at=version.files_removed_at,
        )
