"""Builders of valid domain objects, so tests state only the fields they care about."""

import hashlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from attrs import frozen

from bookreviver.domain.entities import (
    BookPlace,
    BookRecord,
    Job,
    Page,
    PageEdit,
    PageHistoryEntry,
    PageStage,
    PageStepState,
    PageVersion,
    PaginationSection,
    Recipe,
    RecipeProfile,
    Scan,
    Source,
)
from bookreviver.domain.enums import (
    BookResource,
    ChangeSource,
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
    RecipeKind,
    RightsStatus,
    Script,
    SourceKind,
    Stage,
    StageState,
    StepLayer,
    ValueScope,
    ViewMode,
)
from bookreviver.domain.events import BookChanged
from bookreviver.domain.geometry import Rotation
from bookreviver.domain.ids import (
    AccountId,
    BookDirectoryName,
    BookId,
    JobId,
    PageHistoryEntryId,
    PageId,
    PageVersionId,
    PaginationSectionId,
    RecipeId,
    RecipeProfileId,
    ScanId,
    SourceId,
    StepId,
)
from bookreviver.services.book import Book

if TYPE_CHECKING:
    from bookreviver.domain.entities import Actor
    from bookreviver.domain.geometry import EditGeometry
    from bookreviver.domain.values import MetadataMap
    from tests.helpers.database import CatalogueDatabaseFactory, LocatedBookDatabases
    from tests.helpers.storage import SharedBookDirectories
from bookreviver.domain.step_values import StepValues
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
# Largest number of undelivered events each subscriber of a test's event bus keeps
EVENT_QUEUE_SIZE: int = 16
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
# The base step of a page cut from a whole scan, the split being skipped
SPLIT_NONE: ProcessorRef = ProcessorRef(key='split.none', version='1')
# Length of a page version identifier in hexadecimal digits
VERSION_ID_DIGITS: int = 16
# The deskew step of the geometry stage, which the processing tests run
DESKEW: ProcessorRef = ProcessorRef(key='geometry.deskew', version='1')
# The step of a recipe the manual rotation of ``make_page_edit`` is for
DESKEW_STEP_ID: StepId = StepId(UUID('5a1f0000-0000-4000-8000-00000000d5ce'))
# The parameters of the deskew step the builders give a recipe and a profile
DESKEW_PARAMS: MetadataMap = {'max_angle_deg': 5}


def new_account_id() -> AccountId:
    """Return a fresh account identifier.

    :returns: Random identifier of an account that exists nowhere else.
    :rtype: AccountId
    """
    return AccountId(uuid4())


def new_book_id() -> BookId:
    """Return a fresh book identifier.

    :returns: Random identifier of a book that exists nowhere else.
    :rtype: BookId
    """
    return BookId(uuid4())


def directory_of(book_id: BookId) -> BookDirectoryName:
    """Name the directory of a book of a test as the books service names a new one, by the text of its identifier.

    :param book_id: The book.
    :type book_id: BookId
    :returns: The name of the directory of the book.
    :rtype: BookDirectoryName
    """
    return BookDirectoryName(str(book_id))


def make_record(*, title: str = 'Book', minutes: int = 0) -> BookRecord:
    """Build the record of a book updated ``minutes`` after the epoch.

    :param title: Title of the book.
    :type title: str
    :param minutes: Minutes after ``EPOCH`` the book was created and last updated.
    :type minutes: int
    :returns: The record of a book with no source.
    :rtype: BookRecord
    """
    moment = EPOCH + timedelta(minutes=minutes)
    return BookRecord(details=BookDetails(title=title), created_at=moment, updated_at=moment)


@frozen
class BookOpener:
    """Opens the books of a test with their databases, as the access dependencies open them for a request.

    A test that opens a book also seeds the catalogue and reads and writes the databases and the files of the books, so
    the opener hands out the catalogue databases, the databases and the file stores of the test too.

    :ivar catalogue: Function opening a new catalogue database over the catalogue and the books of the test.
    :ivar databases: Opens the databases of the books of the test.
    :ivar files: The directories of the books of the test, with their source stores and asset stores.
    """

    catalogue: CatalogueDatabaseFactory
    databases: LocatedBookDatabases
    files: SharedBookDirectories

    def __call__(self, actor: Actor, book_id: BookId) -> Book:
        """Open a book for the actor, without reading the library.

        :param actor: Account the book is opened for, which the test made its owner.
        :type actor: Actor
        :param book_id: Identifier of the book.
        :type book_id: BookId
        :returns: The open book.
        :rtype: Book
        """
        return open_for(actor, book_id)


def open_for(actor: Actor, book_id: BookId) -> Book:
    """Open a book for the actor as the access dependency ``open_book`` does, without reading the library.

    The book opens its directory and its database by its directory name, so the test has registered them.

    :param actor: Account the book is opened for, which the test made its owner.
    :type actor: Actor
    :param book_id: Identifier of the book.
    :type book_id: BookId
    :returns: The open book.
    :rtype: Book
    """
    return Book(book_id, actor.account_id, directory_of(book_id))


def make_book_change(*, version: int = 1) -> BookChanged:
    """Build a committed change of the description of a book, which a test publishes as any event of the book.

    :param version: The version of the book after the change.
    :type version: int
    :returns: The change.
    :rtype: BookChanged
    """
    return BookChanged(version=version, resources=frozenset({BookResource.RECORD}))


def make_page(
    *,
    order_key: str = 'a0',
    scan: Scan | None = None,
    kind: PageKind = PageKind.TEXT,
    group_label: str = '',
) -> Page:
    """Build an included page of the given book, cut from the whole of a scan or a placeholder without one.

    :param order_key: Order key placing the page in the book, unique within the book.
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

    :param page: Page the section starts at, whose book the section takes.
    :type page: Page
    :param style: How the numbers are written, Arabic unless given.
    :type style: LabelStyle
    :param display: Whether the pages count, and whether their numbers are printed.
    :type display: NumberDisplay
    :param kinds: Kinds of page a series by kind takes, none for a section of the main flow.
    :type kinds: frozenset[PageKind]
    :param minutes: Minutes after ``EPOCH`` the section was made, which orders the sections of a book.
    :type minutes: int
    :returns: A section with a fresh identifier, which a test changes with ``evolve`` for the rest of its fields.
    :rtype: PaginationSection
    """
    moment = EPOCH + timedelta(minutes=minutes)
    return PaginationSection(
        id=PaginationSectionId(uuid4()),
        first_page_id=page.id,
        style=style,
        display=display,
        kinds=kinds,
        created_at=moment,
        updated_at=moment,
    )


def make_source(*, name: str = 'book.pdf', minutes: int = 0) -> Source:
    """Build a PDF source of one file imported ``minutes`` after the epoch, whose digest follows from its name.

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
        kind=SourceKind.PDF,
        file_type=FileType.PDF,
        file_name=name,
        files=[SourceFile(name=name, size_bytes=SOURCE_SIZE_BYTES, sha256=sha256)],
        size_bytes=SOURCE_SIZE_BYTES,
        sha256=sha256,
        scan_count=0,
        imported_at=EPOCH + timedelta(minutes=minutes),
    )


def make_scan(*, source: Source, number: int) -> Scan:
    """Build a grayscale scan of a source.

    :param source: Source holding the scan, whose book the scan belongs to.
    :type source: Source
    :param number: Position of the scan in its source, starting at 0.
    :type number: int
    :returns: A scan with a fresh identifier, fixed pixel size and renditions not yet ready.
    :rtype: Scan
    """
    facts = ScanFacts(width_px=PAGE_WIDTH_PX, height_px=PAGE_HEIGHT_PX, color_mode=ColorMode.GRAY)
    return Scan(id=ScanId(uuid4()), source_id=source.id, number=number, facts=facts)


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
    book_id: BookId,
    state: JobState = JobState.QUEUED,
    minutes: int = 0,
    kind: JobKind = JobKind.IMPORT_SOURCE,
    params: MetadataMap | None = None,
) -> Job:
    """Build a job created ``minutes`` after the epoch, an import job unless told otherwise.

    :param book_id: Book the job imports into.
    :type book_id: BookId
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
        book_id=book_id,
        kind=kind,
        state=state,
        params={} if params is None else params,
        created_at=EPOCH + timedelta(minutes=minutes),
    )


def make_recipe(
    *,
    stage: Stage = Stage.GEOMETRY,
    kind: RecipeKind = RecipeKind.TEXT,
    minutes: int = 0,
) -> Recipe:
    """Build a recipe of one deskew step, created ``minutes`` after the epoch.

    :param stage: Stage the recipe processes.
    :type stage: Stage
    :param kind: Kind of the pages the recipe processes.
    :type kind: RecipeKind
    :param minutes: Minutes after ``EPOCH`` the recipe was created.
    :type minutes: int
    :returns: A recipe with a fresh identifier.
    :rtype: Recipe
    """
    moment = EPOCH + timedelta(minutes=minutes)
    return Recipe(
        id=RecipeId(uuid4()),
        stage=stage,
        kind=kind,
        steps=(Step(processor_key=DESKEW.key, params=DESKEW_PARAMS, step_id=DESKEW_STEP_ID),),
        created_at=moment,
        updated_at=moment,
    )


def make_recipe_profile(
    *,
    account_id: AccountId,
    stage: Stage = Stage.GEOMETRY,
    steps: tuple[Step, ...] | None = None,
    minutes: int = 0,
) -> RecipeProfile:
    """Build a profile of an account named ``Photographed book``, saved ``minutes`` after the epoch.

    :param account_id: Account owning the profile.
    :type account_id: AccountId
    :param stage: Stage whose recipes the profile fits.
    :type stage: Stage
    :param steps: Steps of the profile, or None for one deskew step.
    :type steps: tuple[Step, ...] | None
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
        steps=steps or (Step(processor_key=DESKEW.key, params=DESKEW_PARAMS, step_id=DESKEW_STEP_ID),),
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


def make_book_place(*, stage: Stage = Stage.GEOMETRY, page_id: PageId | None = None) -> BookPlace:
    """Build the place of a reader who zoomed into a page of a stage in the spread layout.

    :param stage: Stage the reader was on.
    :type stage: Stage
    :param page_id: Page that was open, or None for the first page.
    :type page_id: PageId | None
    :returns: A workspace place written at the epoch, with a canvas position and a filter.
    :rtype: BookPlace
    """
    return BookPlace(
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
    :param stage: Stage of the step.
    :type stage: Stage
    :param degrees: Angle the user gave.
    :type degrees: float
    :returns: An edit saved at the epoch.
    :rtype: PageEdit
    """
    return make_geometry_edit(page_id=page_id, geometry=Rotation(degrees=degrees), stage=stage, step_id=DESKEW_STEP_ID)


def make_geometry_edit(
    *, page_id: PageId, geometry: EditGeometry, stage: Stage = Stage.GEOMETRY, step_id: StepId | None = None
) -> PageEdit:
    """Build the manual edit of a page that a geometry processor reads.

    :param page_id: Page the edit belongs to.
    :type page_id: PageId
    :param geometry: Shape the user drew.
    :type geometry: EditGeometry
    :param stage: Stage of the step.
    :type stage: Stage
    :param step_id: The step the edit is for, or None for a new identifier.
    :type step_id: StepId | None
    :returns: An edit saved at the epoch.
    :rtype: PageEdit
    """
    return PageEdit(
        page_id=page_id,
        stage=stage,
        step_id=StepId(uuid4()) if step_id is None else step_id,
        kind=geometry.editor,
        geometry=geometry,
        edit_hash=PageEdit.hash_of(geometry, None),
        updated_at=EPOCH,
    )


def make_page_step_state(
    *,
    page_id: PageId,
    stage: Stage = Stage.GEOMETRY,
    step_id: StepId = DESKEW_STEP_ID,
    params: MetadataMap | None = None,
    edit: PageEdit | None = None,
) -> PageStepState:
    """Build the state of a step on a page, by default the deskew step with no setting and no edit.

    :param page_id: Page the state belongs to.
    :type page_id: PageId
    :param stage: Stage of the step.
    :type stage: Stage
    :param step_id: The step of a recipe.
    :type step_id: StepId
    :param params: The fields the page changes, or None for none.
    :type params: MetadataMap | None
    :param edit: The manual edit of the step, or None.
    :type edit: PageEdit | None
    :returns: A state saved at the epoch.
    :rtype: PageStepState
    """
    return PageStepState(
        page_id=page_id,
        stage=stage,
        step_id=step_id,
        params={} if params is None else params,
        edit=edit,
        updated_at=EPOCH,
    )


def make_page_history_entry(
    *,
    page_id: PageId,
    stage: Stage = Stage.GEOMETRY,
    created_at: datetime = EPOCH,
    before: MetadataMap | None = None,
    after: MetadataMap | None = None,
) -> PageHistoryEntry:
    """Build a change of the settings of the deskew step of a page made by the user.

    :param page_id: Page the change was made on.
    :type page_id: PageId
    :param stage: Stage of the step.
    :type stage: Stage
    :param created_at: When the change was made.
    :type created_at: datetime
    :param before: The settings before the change, or None for none.
    :type before: MetadataMap | None
    :param after: The settings after the change, or None for none.
    :type after: MetadataMap | None
    :returns: A change with a new identifier.
    :rtype: PageHistoryEntry
    """
    return PageHistoryEntry(
        id=PageHistoryEntryId(uuid4()),
        page_id=page_id,
        stage=stage,
        step_id=DESKEW_STEP_ID,
        layer=StepLayer.SETTINGS,
        before=before,
        after=after,
        source=ChangeSource.USER,
        created_at=created_at,
    )


def make_step_values(
    *,
    scope: ValueScope = ValueScope.EVEN,
    group_label: str = '',
    step_id: StepId = DESKEW_STEP_ID,
    params: MetadataMap | None = None,
) -> StepValues:
    """Build the values of a step of the geometry stage for a part of the pages, by default the deskew step, even pages.

    :param scope: The odd pages, the even pages or a group.
    :type scope: ValueScope
    :param group_label: Label of the group, for the scope of a group.
    :type group_label: str
    :param step_id: The step of a recipe.
    :type step_id: StepId
    :param params: The fields the part changes, or None for one field.
    :type params: MetadataMap | None
    :returns: Values saved at the epoch.
    :rtype: StepValues
    """
    return StepValues(
        stage=Stage.GEOMETRY,
        step_id=step_id,
        scope=scope,
        group_label=group_label,
        params={'max_angle_deg': 3} if params is None else params,
        updated_at=EPOCH,
    )
