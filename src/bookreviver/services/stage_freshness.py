"""Whether the current version of a stage on a page is still the one its inputs give, worked out from the inputs alone.

A version is named by the hash of everything it was made from (``VersionInputs.identify``), and a run finds the version
it made before by that hash. So the question whether the result of a stage is up to date is the question whether the
hash of what the stage would read now is the identifier of its current version. ``StageFreshness`` asks it for the
records of a transaction: from the current input of the stage on the page, it works out the identifier of each step of
the recipe of the kind of the page, by ``PageLayers.inputs``, the rule the run itself makes them by, and compares the
last one with the current version of the record. Whatever a change touches, the input of the stage, the recipe, the
settings of the page, of its group or its side, its place in the book, its edits, its kind or the version of a
processor, the hash follows, and a page whose inputs went back to what they were is up to date again.

What the book gives a step that reads it, the fields its parameters leave to the book, depends on the other pages and
is not worked out again here: those fields are taken from the version of the step on the way to the current version, so
a change of another page leaves the page as it was, which is what a stored mark did as well. A stage done by hand and
the page split have no recipe of the book's pages to work out, and a record whose processor is not installed cannot be
worked out, so all of them are taken as they stand. A record that failed stays failed: its freshness is not asked.

The reads are synchronous and go through the repositories of the caller, a fixed number of queries for any number of
records: the pages, the order of the book, the values of its parts, the recipes, the states of the steps and the inputs
of each stage, and the versions on the way to the step that reads the book, a level at a time.
"""

import json
from typing import TYPE_CHECKING

from attrs import evolve

from bookreviver.core.content import BookContent
from bookreviver.domain.errors import InvalidParametersError, NotFoundError
from bookreviver.domain.ids import PageVersionId
from bookreviver.domain.page_steps import PageStepKey
from bookreviver.domain.stages import Stage, StageState
from bookreviver.services.book_steps import BookSteps
from bookreviver.services.step_params import PageLayers, StepRead

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from bookreviver.core.plugins import Processor, ProcessorCatalog
    from bookreviver.domain.ids import PageId, RecipeId
    from bookreviver.domain.metadata import MetadataMap
    from bookreviver.domain.page_steps import PageStepState
    from bookreviver.domain.pages import Page
    from bookreviver.domain.recipes import Recipe, RecipeKind, Step
    from bookreviver.domain.stages import PageStage, PageStageKey
    from bookreviver.domain.step_values import StepValues
    from bookreviver.ports.persistence import BookRepositories


class StageFreshness:
    """Works out whether stage records are up to date from the inputs of their stages, built once by the container."""

    def __init__(self, *, processors: ProcessorCatalog) -> None:
        """Work with the processors of the application.

        :param processors: The processors the application can run, with the versions installed.
        :type processors: ProcessorCatalog
        """
        self.processors = processors
        self.book_steps = BookSteps(processors)

    def stale(self, repos: BookRepositories, records: Sequence[PageStage]) -> set[PageStageKey]:
        """Find the records whose current version is not the one the inputs of their stage give now.

        :param repos: Repositories of the transaction the caller runs in, which are only read.
        :type repos: BookRepositories
        :param records: Records read in that transaction.
        :type records: Sequence[PageStage]
        :returns: The keys of the records that are out of date. A record that failed is never among them.
        :rtype: set[PageStageKey]
        """
        return FreshnessWork(self, repos, records).stale()


class FreshnessWork:
    """The freshness of the records of one transaction, over what is read for all of them once."""

    def __init__(self, freshness: StageFreshness, repos: BookRepositories, records: Sequence[PageStage]) -> None:
        """Take the records to judge.

        :param freshness: What the work belongs to, which holds the processors and the recipes.
        :type freshness: StageFreshness
        :param repos: Repositories of the transaction, which are only read.
        :type repos: BookRepositories
        :param records: The records to judge.
        :type records: Sequence[PageStage]
        """
        self.freshness = freshness
        self.repos = repos
        self.records = [
            record
            for record in records
            if record.state is not StageState.FAILED
            and record.head_version_id is not None
            and not record.stage.manual
            and record.stage is not Stage.PAGE_SPLIT
        ]
        self._checked: dict[tuple[str, str], MetadataMap] = {}
        self._parts: list[StepValues] | None = None
        self._positions: dict[PageId, int] | None = None

    def stale(self) -> set[PageStageKey]:
        """Judge every record, a stage at a time.

        :returns: The keys of the records that are out of date.
        :rtype: set[PageStageKey]
        """
        if not self.records:
            return set()
        pages = {page.id: page for page in self.repos.pages.list_by_ids({record.page_id for record in self.records})}
        stale: set[PageStageKey] = set()
        for stage in {record.stage for record in self.records}:
            of_stage = [record for record in self.records if record.stage is stage and record.page_id in pages]
            stale |= StageJudgement(self, stage, of_stage, pages).stale()
        return stale

    def check(self, processor: Processor, params: MetadataMap) -> MetadataMap:
        """Check the parameters of a processor once for every set of them, since most pages share theirs.

        :param processor: The processor.
        :type processor: Processor
        :param params: The parameters laid for a page.
        :type params: MetadataMap
        :returns: The parameters checked, with the defaults filled in.
        :rtype: MetadataMap
        :raises InvalidParametersError: If the parameters do not fit the processor.
        """
        key = (processor.spec.key, json.dumps(params, sort_keys=True, default=str))
        if (checked := self._checked.get(key)) is None:
            checked = self._checked[key] = processor.validate_params(params)
        return checked

    def current(self, recipe: Recipe) -> Recipe:
        """Give a stored recipe with the parameters of each step as its processor writes them now, as a run reads it.

        A step whose processor is gone or refuses its stored parameters is given as stored, as ``RecipeBook`` does.

        :param recipe: The recipe as stored.
        :type recipe: Recipe
        :returns: The recipe with the checked parameters of each step it could check.
        :rtype: Recipe
        """
        steps: list[Step] = []
        for step in recipe.steps:
            try:
                steps.append(
                    evolve(step, params=self.check(self.freshness.processors.get(step.processor_key), step.params))
                )
            except NotFoundError, InvalidParametersError:
                steps.append(step)
        return evolve(recipe, steps=tuple(steps))

    def layers(self, page: Page) -> PageLayers:
        """Give the layers of a page over the values of the parts of the book and the order read once.

        :param page: The page.
        :type page: Page
        :returns: The layers of the page.
        :rtype: PageLayers
        """
        if self._parts is None:
            self._parts = list(self.repos.step_values.list_all())
        position = None
        # One page counts its place by one query, and many read the order of the book once
        if self._parts and len(self.records) > 1:
            if self._positions is None:
                order = self.repos.pages.order().page_ids
                self._positions = {page_id: place + 1 for place, page_id in enumerate(order)}
            position = self._positions.get(page.id)
        return PageLayers(page, parts=self._parts, position=position, check=self.check)


class StageJudgement:
    """The records of one stage, judged against the recipes of the stage and the states of their steps."""

    def __init__(
        self, work: FreshnessWork, stage: Stage, records: Sequence[PageStage], pages: Mapping[PageId, Page]
    ) -> None:
        """Take the records of the stage.

        :param work: The work of the transaction.
        :type work: FreshnessWork
        :param stage: The stage.
        :type stage: Stage
        :param records: The records of the stage, whose pages are among ``pages``.
        :type records: Sequence[PageStage]
        :param pages: The pages of the records, by identifier.
        :type pages: Mapping[PageId, Page]
        """
        self.work = work
        self.stage = stage
        self.records = records
        self.pages = pages
        repos = work.repos
        self.recipes: dict[RecipeKind, Recipe] = {
            recipe.kind: work.current(recipe) for recipe in repos.recipes.list_for_stage(stage)
        }
        self.inputs = BookContent(repos).pages.inputs(stage, [record.page_id for record in records])
        self._states: dict[PageStepKey, PageStepState] = {}
        self._states_read: set[RecipeId] = set()

    def stale(self) -> set[PageStageKey]:
        """Judge the records of the stage.

        :returns: The keys of those that are out of date.
        :rtype: set[PageStageKey]
        """
        chains = BookChains(self)
        return {
            record.key for record in self.records if self.expected(record, chains) not in {record.head_version_id, None}
        }

    def expected(self, record: PageStage, chains: BookChains) -> PageVersionId | None:
        """Work out the identifier the current version of a record would have if the stage ran now.

        :param record: The record.
        :type record: PageStage
        :param chains: The versions on the way to the step that reads the book, of the records that have one.
        :type chains: BookChains
        :returns: The identifier, the current version itself when it cannot be worked out, or an empty identifier
                  when the stage would not reach a version at all, which is never the current one.
        :rtype: PageVersionId | None
        """
        page = self.pages[record.page_id]
        if (recipe := self.recipes.get(page.recipe_kind)) is None:
            return None
        steps = self.steps(record, recipe)
        if not steps or (source := self.inputs.get(record.page_id)) is None:
            return PageVersionId('')
        layers = self.work.layers(page)
        input_id: PageVersionId | None = source.id
        try:
            for place, step in enumerate(steps):
                processor = self.work.freshness.processors.get(step.processor_key)
                read = StepRead(input_id=input_id, book=chains.book_of(record, place, step, processor))
                state = None if page.is_leaf else self.state(page.id, recipe, step)
                input_id = layers.inputs(self.work.repos, step, processor, state, read).identify()
        except NotFoundError:
            return None
        except InvalidParametersError:
            return PageVersionId('')
        return input_id

    def steps(self, record: PageStage, recipe: Recipe) -> tuple[Step, ...]:
        """Give the steps a run of the recipe made the current version of a record by.

        :param record: The record.
        :type record: PageStage
        :param recipe: The recipe of the kind of the page.
        :type recipe: Recipe
        :returns: The steps that are on, up to the one the record stopped at.
        :rtype: tuple[Step, ...]
        """
        return tuple(step for _, step in recipe.indexed_steps_through(record.through_step_id))

    def state(self, page_id: PageId, recipe: Recipe, step: Step) -> PageStepState | None:
        """Give what a page keeps for a step, reading the states of every step of the recipe once.

        :param page_id: The page.
        :type page_id: PageId
        :param recipe: The recipe of the step.
        :type recipe: Recipe
        :param step: The step.
        :type step: Step
        :returns: The state, or None for a page that keeps nothing for the step.
        :rtype: PageStepState | None
        """
        if recipe.id not in self._states_read:
            self._states_read.add(recipe.id)
            page_ids = [record.page_id for record in self.records]
            for each in recipe.steps:
                for state in self.work.repos.page_step_states.list_for_step(page_ids, self.stage, each.step_id):
                    self._states[PageStepKey(state.page_id, state.stage, state.step_id)] = state
        return self._states.get(PageStepKey(page_id, self.stage, step.step_id))


class BookChains:
    """The versions the step that reads the book made on the way to the current versions of the records of a stage."""

    def __init__(self, judgement: StageJudgement) -> None:
        """Read the versions of the records whose recipe has a step that reads the book, a level at a time.

        :param judgement: The records of the stage.
        :type judgement: StageJudgement
        """
        self._judgement = judgement
        # The place of the step that reads the book among the steps of each record that has one
        self._places: dict[PageStageKey, int] = {}
        # The parameters of the version that step made on the way to the current version of each record
        self._made: dict[PageStageKey, MetadataMap] = {}
        self._walk(self._starts())

    def _starts(self) -> dict[PageStageKey, tuple[PageVersionId, int]]:
        """Find the records with a step that reads the book, with how many versions back from the current one it is.

        :returns: The current version of each such record and the number of versions to walk back.
        :rtype: dict[PageStageKey, tuple[PageVersionId, int]]
        """
        judgement = self._judgement
        book_steps = judgement.work.freshness.book_steps
        starts: dict[PageStageKey, tuple[PageVersionId, int]] = {}
        for record in judgement.records:
            recipe = judgement.recipes.get(judgement.pages[record.page_id].recipe_kind)
            steps = () if recipe is None else judgement.steps(record, recipe)
            place = book_steps.position(steps)
            if place is not None and record.head_version_id is not None:
                self._places[record.key] = place
                starts[record.key] = (record.head_version_id, len(steps) - 1 - place)
        return starts

    def _walk(self, current: dict[PageStageKey, tuple[PageVersionId, int]]) -> None:
        """Walk back from the current versions, one level of all the chains for each read.

        :param current: The version each chain stands on and the number of versions still to walk back.
        :type current: dict[PageStageKey, tuple[PageVersionId, int]]
        """
        while current:
            current = self._level(current)

    def _level(
        self, current: dict[PageStageKey, tuple[PageVersionId, int]]
    ) -> dict[PageStageKey, tuple[PageVersionId, int]]:
        """Read one level of the chains, keep the parameters of the chains that reached the step, and step the others.

        A chain whose version is gone, or that reaches a base version first, ends with nothing kept.

        :param current: The version each chain stands on and the number of versions still to walk back.
        :type current: dict[PageStageKey, tuple[PageVersionId, int]]
        :returns: The chains that go on, each standing on the version before.
        :rtype: dict[PageStageKey, tuple[PageVersionId, int]]
        """
        ids = {version_id for version_id, _ in current.values()}
        versions = {version.id: version for version in self._judgement.work.repos.page_versions.list_by_ids(ids)}
        following: dict[PageStageKey, tuple[PageVersionId, int]] = {}
        for key, (version_id, back) in current.items():
            version = versions.get(version_id)
            if version is not None and back == 0:
                self._made[key] = version.params
            elif version is not None and version.input_id is not None:
                following[key] = (version.input_id, back - 1)
        return following

    def book_of(self, record: PageStage, place: int, step: Step, processor: Processor) -> MetadataMap | None:
        """Give the fields the book gave a step when it made the version on the way to the current one of a record.

        :param record: The record.
        :type record: PageStage
        :param place: Place of the step among the steps of the run.
        :type place: int
        :param step: The step.
        :type step: Step
        :param processor: The processor of the step.
        :type processor: Processor
        :returns: The fields the parameters of the step leave to the book, which the value 0 does, with the values the
                  version has, or None for a step that does not read the book or leaves it nothing.
        :rtype: MetadataMap | None
        """
        made = self._made.get(record.key)
        book_step = self._judgement.work.freshness.book_steps.of(step)
        if made is None or book_step is None or place != self._places.get(record.key):
            return None
        params = self._judgement.work.check(processor, step.params)
        if not book_step.leaves_to_book(params):
            return None
        return {name: made[name] for name in processor.spec.book_fields if name in made and not params.get(name)}
