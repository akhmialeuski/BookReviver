"""Builders of valid domain objects, so tests state only the fields they care about."""

import hashlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from attrs import evolve

from bookreviver.domain.entities import (
    BookPlace,
    Job,
    Page,
    PageEdit,
    PageStage,
    PageVersion,
    PaginationSection,
    Project,
    Recipe,
    RecipeProfile,
    RecipeRule,
    Scan,
    Source,
)
from bookreviver.domain.enums import (
    ColorMode,
    CompareMode,
    ContributorRole,
    FileType,
    IdentifierScheme,
    JobKind,
    JobState,
    LabelStyle,
    NumberDisplay,
    Orthography,
    PageFilter,
    PageKind,
    PageOrigin,
    PlaceMode,
    RightsStatus,
    RuleCondition,
    Script,
    SourceKind,
    Stage,
    StageState,
    ViewMode,
)
from bookreviver.domain.geometry import Rotation
from bookreviver.domain.ids import (
    AccountId,
    JobId,
    PageId,
    PageVersionId,
    PaginationSectionId,
    ProjectId,
    RecipeId,
    RecipeProfileId,
    RecipeRuleId,
    ScanId,
    SourceId,
)

if TYPE_CHECKING:
    from bookreviver.domain.geometry import EditGeometry
    from bookreviver.domain.values import MetadataMap
from bookreviver.domain.values import (
    BookDetails,
    BookIdentifier,
    CanvasPosition,
    Contributor,
    ProcessorRef,
    ScanFacts,
    SourceFile,
    Step,
)

EPOCH: datetime = datetime(2026, 1, 1, tzinfo=UTC)
# A description with every field filled in, in the shape of a pre-reform Belarusian edition
FULL_DETAILS: BookDetails = BookDetails(
    title='Беларускія народныя казкі',
    subtitle='съ примѣчаніями',
    parallel_titles=('Białoruskie baśnie ludowe',),
    original_title='Народныя казкі',
    contributors=(
        Contributor(name='Я. Карскі', role=ContributorRole.AUTHOR),
        Contributor(name='И. И. Ивановъ', role=ContributorRole.EDITOR),
        Contributor(name='П. П. Петровъ', role=ContributorRole.ENGRAVER),
    ),
    publisher='Изданіе автора',
    printer='Типографія губернскаго правленія',
    publication_place='Вільня',
    publication_year='[1905]',
    edition='Выданне другое',
    censorship='Дозволено цензурою. Вильна, 12 мая 1905',
    series='Этнаграфічны зборнік',
    series_number='12',
    volume='II',
    languages=('bel', 'rus'),
    orthography=Orthography.PRE_REFORM,
    script=Script.CYRILLIC,
    printed_pagination='XII, 340 стр., 8 л. ил.',
    height_cm=22,
    illustrations='8 л. ил., 1 л. карт.',
    binding='Издательская обложка',
    identifiers=(
        BookIdentifier.parse(IdentifierScheme.SHELFMARK, '18.123.4.56'),
        BookIdentifier.parse(IdentifierScheme.ISBN, '0-306-40615-2'),
        BookIdentifier.parse(IdentifierScheme.URL, 'https://example.org/books/karski'),
    ),
    subjects=('Фольклор', 'Народные песни'),
    rights=RightsStatus.PUBLIC_DOMAIN,
    copy_holder='Частное собрание',
    copy_notes='Экслибрис на форзаце, карандашные пометы',
    notes='Scanned from the library copy',
)
PAGE_WIDTH_PX: int = 2200
PAGE_HEIGHT_PX: int = 1561
SOURCE_SIZE_BYTES: int = 4096
SOURCE_SCAN_COUNT: int = 12
# The base step of a page cut from a whole scan, the split being skipped
SPLIT_NONE: ProcessorRef = ProcessorRef(key='split.none', version='1')
# Length of a page version identifier in hexadecimal digits
VERSION_ID_DIGITS: int = 16
# The deskew step of the geometry stage, which the processing tests run
DESKEW: ProcessorRef = ProcessorRef(key='geometry.deskew', version='1')
# The parameters of the deskew step the builders give a recipe and a profile
DESKEW_PARAMS: MetadataMap = {'max_angle_deg': 5}


def new_account_id() -> AccountId:
    """Return a fresh account identifier.

    :returns: Random identifier of an account that exists nowhere else.
    :rtype: AccountId
    """
    return AccountId(uuid4())


def make_project(*, owner_id: AccountId, title: str = 'Book', minutes: int = 0) -> Project:
    """Build a project updated ``minutes`` after the epoch.

    :param owner_id: Account owning the project.
    :type owner_id: AccountId
    :param title: Title of the book.
    :type title: str
    :param minutes: Minutes after ``EPOCH`` the project was created and last updated.
    :type minutes: int
    :returns: A project with a fresh identifier and no source.
    :rtype: Project
    """
    moment = EPOCH + timedelta(minutes=minutes)
    return Project(
        id=ProjectId(uuid4()),
        owner_id=owner_id,
        details=BookDetails(title=title),
        created_at=moment,
        updated_at=moment,
    )


def make_page(
    *,
    project_id: ProjectId,
    order_key: str = 'a0',
    scan: Scan | None = None,
    kind: PageKind = PageKind.TEXT,
    group_label: str = '',
) -> Page:
    """Build an included page of the given project, cut from the whole of a scan or a placeholder without one.

    :param project_id: Project owning the page.
    :type project_id: ProjectId
    :param order_key: Order key placing the page in the book, unique within the project.
    :type order_key: str
    :param scan: Scan the page shows whole, or None for a placeholder.
    :type scan: Scan | None
    :param kind: Role of the page in the book, a text page unless given.
    :type kind: PageKind
    :param group_label: Label of the group the user put the page in, or empty.
    :type group_label: str
    :returns: A page with a fresh identifier, created and updated at the epoch.
    :rtype: Page
    """
    return Page(
        id=PageId(uuid4()),
        project_id=project_id,
        order_key=order_key,
        kind=kind,
        origin=PageOrigin.PLACEHOLDER if scan is None else PageOrigin.SCAN,
        scan_id=None if scan is None else scan.id,
        group_label=group_label,
        created_at=EPOCH,
        updated_at=EPOCH,
    )


def make_section(
    *,
    page: Page,
    style: LabelStyle = LabelStyle.ARABIC,
    display: NumberDisplay = NumberDisplay.PRINTED,
    kinds: frozenset[PageKind] = frozenset(),
    minutes: int = 0,
) -> PaginationSection:
    """Build a pagination section that starts at a page, numbering from 1 with no prefix and no name.

    :param page: Page the section starts at, whose project the section takes.
    :type page: Page
    :param style: How the numbers are written, Arabic unless given.
    :type style: LabelStyle
    :param display: Whether the pages count, and whether their numbers are printed.
    :type display: NumberDisplay
    :param kinds: Kinds of page a series by kind takes, none for a section of the main flow.
    :type kinds: frozenset[PageKind]
    :param minutes: Minutes after ``EPOCH`` the section was made, which orders the sections of a project.
    :type minutes: int
    :returns: A section with a fresh identifier, which a test changes with ``evolve`` for the rest of its fields.
    :rtype: PaginationSection
    """
    moment = EPOCH + timedelta(minutes=minutes)
    return PaginationSection(
        id=PaginationSectionId(uuid4()),
        project_id=page.project_id,
        first_page_id=page.id,
        style=style,
        display=display,
        kinds=kinds,
        created_at=moment,
        updated_at=moment,
    )


def make_source(*, project_id: ProjectId, name: str = 'book.pdf', minutes: int = 0) -> Source:
    """Build a PDF source of one file imported ``minutes`` after the epoch, whose digest follows from its name.

    :param project_id: Project owning the source.
    :type project_id: ProjectId
    :param name: Name of the uploaded file; sources of different names have different digests.
    :type name: str
    :param minutes: Minutes after ``EPOCH`` the source was imported.
    :type minutes: int
    :returns: A source with a fresh identifier and no import job.
    :rtype: Source
    """
    sha256 = hashlib.sha256(name.encode()).hexdigest()
    return Source(
        id=SourceId(uuid4()),
        project_id=project_id,
        kind=SourceKind.PDF,
        file_type=FileType.PDF,
        file_name=name,
        files=[SourceFile(name=name, size_bytes=SOURCE_SIZE_BYTES, sha256=sha256)],
        size_bytes=SOURCE_SIZE_BYTES,
        sha256=sha256,
        scan_count=SOURCE_SCAN_COUNT,
        imported_at=EPOCH + timedelta(minutes=minutes),
    )


def make_scan(*, source: Source, number: int) -> Scan:
    """Build a grayscale scan of a source.

    :param source: Source holding the scan, whose project the scan belongs to.
    :type source: Source
    :param number: Position of the scan in its source, starting at 0.
    :type number: int
    :returns: A scan with a fresh identifier, fixed pixel size and renditions not yet ready.
    :rtype: Scan
    """
    facts = ScanFacts(width_px=PAGE_WIDTH_PX, height_px=PAGE_HEIGHT_PX, color_mode=ColorMode.GRAY)
    return Scan(id=ScanId(uuid4()), project_id=source.project_id, source_id=source.id, number=number, facts=facts)


def make_page_version(*, page_id: PageId, minutes: int = 0) -> PageVersion:
    """Build the base version of a page cut from a whole scan, created ``minutes`` after the epoch.

    :param page_id: Page the version belongs to.
    :type page_id: PageId
    :param minutes: Minutes after ``EPOCH`` the version was created.
    :type minutes: int
    :returns: A pending ``split.none`` version with a random identifier and renditions not yet ready.
    :rtype: PageVersion
    """
    return PageVersion(
        id=PageVersionId(uuid4().hex[:VERSION_ID_DIGITS]),
        page_id=page_id,
        stage=Stage.PAGE_SPLIT,
        processor=SPLIT_NONE,
        created_at=EPOCH + timedelta(minutes=minutes),
    )


def make_job(
    *,
    project_id: ProjectId,
    state: JobState = JobState.QUEUED,
    minutes: int = 0,
    kind: JobKind = JobKind.IMPORT_SOURCE,
    params: MetadataMap | None = None,
) -> Job:
    """Build a job created ``minutes`` after the epoch, an import job unless told otherwise.

    :param project_id: Project the job imports into.
    :type project_id: ProjectId
    :param state: State of the job.
    :type state: JobState
    :param minutes: Minutes after ``EPOCH`` the job was created.
    :type minutes: int
    :param kind: What the job does.
    :type kind: JobKind
    :param params: What a processing job was asked to do, or None for a job without.
    :type params: MetadataMap | None
    :returns: A job with a fresh identifier.
    :rtype: Job
    """
    return Job(
        id=JobId(uuid4()),
        project_id=project_id,
        kind=kind,
        state=state,
        params={} if params is None else params,
        created_at=EPOCH + timedelta(minutes=minutes),
    )


def make_recipe(
    *,
    project_id: ProjectId,
    stage: Stage = Stage.GEOMETRY,
    name: str = 'Deskew',
    active: bool = False,
    minutes: int = 0,
) -> Recipe:
    """Build a recipe of one deskew step, created ``minutes`` after the epoch.

    :param project_id: Project owning the recipe.
    :type project_id: ProjectId
    :param stage: Stage the recipe processes.
    :type stage: Stage
    :param name: Name of the recipe.
    :type name: str
    :param active: Whether the recipe is the active one of its stage.
    :type active: bool
    :param minutes: Minutes after ``EPOCH`` the recipe was created.
    :type minutes: int
    :returns: A recipe with a fresh identifier.
    :rtype: Recipe
    """
    moment = EPOCH + timedelta(minutes=minutes)
    return Recipe(
        id=RecipeId(uuid4()),
        project_id=project_id,
        stage=stage,
        name=name,
        steps=(Step(processor_key=DESKEW.key, params=DESKEW_PARAMS),),
        active=active,
        created_at=moment,
        updated_at=moment,
    )


def make_recipe_profile(
    *,
    account_id: AccountId,
    stage: Stage = Stage.GEOMETRY,
    steps: tuple[Step, ...] | None = None,
    is_default: bool = False,
    minutes: int = 0,
) -> RecipeProfile:
    """Build a profile of an account named ``Photographed book``, saved ``minutes`` after the epoch.

    :param account_id: Account owning the profile.
    :type account_id: AccountId
    :param stage: Stage whose recipes the profile fits.
    :type stage: Stage
    :param steps: Steps of the profile, or None for one deskew step.
    :type steps: tuple[Step, ...] | None
    :param is_default: Whether the profile is the default of its stage.
    :type is_default: bool
    :param minutes: Minutes after ``EPOCH`` the profile was saved.
    :type minutes: int
    :returns: A profile with a fresh identifier.
    :rtype: RecipeProfile
    """
    moment = EPOCH + timedelta(minutes=minutes)
    return RecipeProfile(
        id=RecipeProfileId(uuid4()),
        account_id=account_id,
        stage=stage,
        name='Photographed book',
        steps=steps or (Step(processor_key=DESKEW.key, params=DESKEW_PARAMS),),
        is_default=is_default,
        created_at=moment,
        updated_at=moment,
    )


def make_page_stage(
    *,
    page_id: PageId,
    stage: Stage = Stage.GEOMETRY,
    recipe_id: RecipeId | None = None,
    head_version_id: PageVersionId | None = None,
    state: StageState = StageState.FRESH,
) -> PageStage:
    """Build the record of a stage of a page.

    :param page_id: Page the record belongs to.
    :type page_id: PageId
    :param stage: The stage.
    :type stage: Stage
    :param recipe_id: Recipe the page was processed by.
    :type recipe_id: RecipeId | None
    :param head_version_id: Current version of the stage.
    :type head_version_id: PageVersionId | None
    :param state: Whether the current version is up to date.
    :type state: StageState
    :returns: A record changed at the epoch.
    :rtype: PageStage
    """
    return PageStage(
        page_id=page_id,
        stage=stage,
        recipe_id=recipe_id,
        head_version_id=head_version_id,
        state=state,
        updated_at=EPOCH,
    )


def make_pinned_stage(*, page_id: PageId, recipe_id: RecipeId | None, stage: Stage = Stage.GEOMETRY) -> PageStage:
    """Build the record of a stage of a page whose recipe the user pinned to it.

    :param page_id: Page the record belongs to.
    :type page_id: PageId
    :param recipe_id: Recipe pinned to the page.
    :type recipe_id: RecipeId | None
    :param stage: The stage.
    :type stage: Stage
    :returns: A pinned record changed at the epoch.
    :rtype: PageStage
    """
    return evolve(make_page_stage(page_id=page_id, stage=stage, recipe_id=recipe_id), pinned=True)


def make_recipe_rule(
    *,
    recipe: Recipe,
    condition: RuleCondition = RuleCondition.PLATES,
    group_label: str = '',
    order: int = 0,
) -> RecipeRule:
    """Build a rule that sends the pages meeting a condition to a recipe, of the stage and the project of the recipe.

    :param recipe: Recipe the rule names, whose project and stage the rule takes.
    :type recipe: Recipe
    :param condition: What a page must be for the rule to match it.
    :type condition: RuleCondition
    :param group_label: The group a page must be in, for the condition on a manual group.
    :type group_label: str
    :param order: Place of the rule among the rules of the stage.
    :type order: int
    :returns: A rule with a fresh identifier.
    :rtype: RecipeRule
    """
    return RecipeRule(
        id=RecipeRuleId(uuid4()),
        project_id=recipe.project_id,
        stage=recipe.stage,
        condition=condition,
        group_label=group_label,
        recipe_id=recipe.id,
        order=order,
    )


def make_book_place(
    *, account_id: AccountId, project_id: ProjectId, stage: Stage = Stage.GEOMETRY, page_id: PageId | None = None
) -> BookPlace:
    """Build the place of a reader who zoomed into a page of a stage in the spread layout.

    :param account_id: Account the place belongs to.
    :type account_id: AccountId
    :param project_id: Book the place is in.
    :type project_id: ProjectId
    :param stage: Stage the reader was on.
    :type stage: Stage
    :param page_id: Page that was open, or None for the first page.
    :type page_id: PageId | None
    :returns: A workspace place written at the epoch, with a canvas position and a filter.
    :rtype: BookPlace
    """
    return BookPlace(
        account_id=account_id,
        project_id=project_id,
        mode=PlaceMode.WORKSPACE,
        stage=stage,
        page_id=page_id,
        view=ViewMode.SPREAD,
        compare=CompareMode.SWIPE,
        filter=PageFilter.CHECK,
        canvas=CanvasPosition(zoom=2.5, centre_x=0.4, centre_y=0.6),
        strip_page_id=page_id,
        updated_at=EPOCH,
    )


def make_page_edit(*, page_id: PageId, stage: Stage = Stage.GEOMETRY, degrees: float = 1.5) -> PageEdit:
    """Build the manual rotation of a page that the deskew processor reads.

    :param page_id: Page the edit belongs to.
    :type page_id: PageId
    :param stage: Stage of the processor.
    :type stage: Stage
    :param degrees: Angle the user gave.
    :type degrees: float
    :returns: An edit saved at the epoch.
    :rtype: PageEdit
    """
    return make_geometry_edit(
        page_id=page_id, processor_key=DESKEW.key, geometry=Rotation(degrees=degrees), stage=stage
    )


def make_geometry_edit(
    *, page_id: PageId, processor_key: str, geometry: EditGeometry, stage: Stage = Stage.GEOMETRY
) -> PageEdit:
    """Build the manual edit of a page that a geometry processor reads.

    :param page_id: Page the edit belongs to.
    :type page_id: PageId
    :param processor_key: Key of the processor the edit is for.
    :type processor_key: str
    :param geometry: Shape the user drew.
    :type geometry: EditGeometry
    :param stage: Stage of the processor.
    :type stage: Stage
    :returns: An edit saved at the epoch.
    :rtype: PageEdit
    """
    return PageEdit(
        page_id=page_id,
        stage=stage,
        processor_key=processor_key,
        kind=geometry.editor,
        geometry=geometry,
        edit_hash=PageEdit.hash_of(geometry, None),
        updated_at=EPOCH,
    )
