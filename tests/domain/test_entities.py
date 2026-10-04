"""Tests for the domain entities and values."""

import hashlib
import json
import re
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.entities import VERSION_ID_PATTERN, PageEdit, PageStepChange, VersionInputs
from bookreviver.domain.enums import (
    AppliesTo,
    BlankFill,
    ChangeSource,
    ColorMode,
    JobKind,
    PageKind,
    PageOrigin,
    PageSide,
    Rendition,
    Stage,
    StepField,
    StepLayer,
    VersionScale,
)
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.geometry import Line, Point, Rotation
from bookreviver.domain.history import StepHistory
from bookreviver.domain.ids import PageId, PageStepChangeId, PageVersionId, ScanId, StorageKey
from bookreviver.domain.values import BookDetails, ProcessorRef, Progress, Renditions, StageRun, Step
from tests.helpers.builders import (
    EPOCH,
    SPLIT_NONE,
    make_job,
    make_page,
    make_page_edit,
    make_page_step_change,
    make_page_step_state,
    make_page_version,
    make_project,
    make_recipe,
    make_scan,
    make_source,
    new_account_id,
)

if TYPE_CHECKING:
    from bookreviver.domain.entities import Recipe

PAGE_ID: PageId = PageId(uuid4())
DESKEW_KEY: str = 'geometry.deskew'


class TestJobStage:
    """Tests for Job.stage."""

    def test_a_run_stage_job_names_the_stage_of_its_parameters(self) -> None:
        """Verify the stage comes out of the parameters the job stores."""
        job = make_job(
            project_id=make_project(owner_id=new_account_id()).id,
            kind=JobKind.RUN_STAGE,
            params=StageRun(stage=Stage.CLEANUP).to_map(),
        )
        assert job.stage is Stage.CLEANUP

    def test_a_job_of_another_kind_has_no_stage(self) -> None:
        """Verify an import job has no stage, whatever its parameters hold."""
        job = make_job(
            project_id=make_project(owner_id=new_account_id()).id, params=StageRun(stage=Stage.CLEANUP).to_map()
        )
        assert job.stage is None

    def test_a_run_stage_job_with_unreadable_parameters_has_no_stage(self) -> None:
        """Verify parameters that are not those of a stage run give no stage and no error."""
        job = make_job(project_id=make_project(owner_id=new_account_id()).id, kind=JobKind.RUN_STAGE)
        assert job.stage is None


class TestPage:
    """Tests for Page invariants."""

    @pytest.mark.parametrize('origin', [PageOrigin.BLANK, PageOrigin.PLACEHOLDER])
    def test_page_not_cut_from_a_scan_names_no_scan(self, origin: PageOrigin) -> None:
        """Reject a blank leaf or a placeholder that names a scan, which only a page cut from one may.

        :param origin: Origin of a page that has no scan.
        :type origin: PageOrigin
        """
        page = make_page(project_id=make_project(owner_id=new_account_id()).id)
        with pytest.raises(ValueError, match='has no scan'):
            evolve(page, origin=origin, scan_id=ScanId(uuid4()))

    @pytest.mark.parametrize('fill', [BlankFill.WHITE, BlankFill.PAPER])
    def test_a_leaf_stands_in_place_of_the_scan_of_a_blank_page_cut_from_a_scan(self, fill: BlankFill) -> None:
        """Verify a blank page cut from a scan may show either leaf, and then is a leaf for the stages.

        :param fill: The leaf the page shows.
        :type fill: BlankFill
        """
        project = make_project(owner_id=new_account_id())
        scan = make_scan(source=make_source(project_id=project.id), number=0)
        page = make_page(project_id=project.id, scan=scan, kind=PageKind.BLANK)

        assert evolve(page, blank_fill=fill).is_leaf

    def test_a_page_that_keeps_its_scan_is_no_leaf_and_a_generated_page_is_one(self) -> None:
        """Verify the scan is no leaf, and a page generated as a white leaf is one without choosing anything."""
        project = make_project(owner_id=new_account_id())
        scan = make_scan(source=make_source(project_id=project.id), number=0)
        scanned = make_page(project_id=project.id, scan=scan, kind=PageKind.BLANK)

        assert not scanned.is_leaf
        assert evolve(make_page(project_id=project.id), origin=PageOrigin.BLANK).is_leaf

    @pytest.mark.parametrize(
        ('kind', 'origin'),
        [
            (PageKind.TEXT, PageOrigin.SCAN),
            (PageKind.BLANK, PageOrigin.PLACEHOLDER),
            (PageKind.BLANK, PageOrigin.BLANK),
        ],
        ids=['text-page', 'placeholder', 'generated-leaf'],
    )
    def test_a_leaf_is_refused_where_there_is_no_blank_scan_to_replace(
        self, kind: PageKind, origin: PageOrigin
    ) -> None:
        """Reject a leaf on a page that is not blank, and on a page that is cut from no scan.

        :param kind: Kind of the page.
        :type kind: PageKind
        :param origin: Where the image of the page comes from.
        :type origin: PageOrigin
        """
        project = make_project(owner_id=new_account_id())
        scan = make_scan(source=make_source(project_id=project.id), number=0) if origin is PageOrigin.SCAN else None
        page = evolve(make_page(project_id=project.id, scan=scan, kind=kind), origin=origin)

        with pytest.raises(ValueError, match='blank page cut from a scan'):
            evolve(page, blank_fill=BlankFill.WHITE)

    def test_page_whose_source_was_deleted_keeps_its_origin(self) -> None:
        """Verify a page cut from a scan stays one when its scan is gone, since it keeps its own copy of the image."""
        page = evolve(make_page(project_id=make_project(owner_id=new_account_id()).id), origin=PageOrigin.SCAN)
        assert (page.origin, page.scan_id) == (PageOrigin.SCAN, None)


class TestPageVersion:
    """Tests for PageVersion invariants."""

    def test_renditions_past_their_first_version_are_rejected(self) -> None:
        """Verify a version's files are written once into its own directory, so they never get a second version."""
        version = make_page_version(page_id=PageId(uuid4()))
        with pytest.raises(ValueError, match='written once'):
            evolve(version, renditions=Renditions(version=Renditions.FIRST_VERSION + 1))

    def test_step_without_an_image_has_no_renditions(self) -> None:
        """Verify a version of a step without an image, such as recognition, carries no renditions."""
        version = make_page_version(page_id=PageId(uuid4()))
        assert evolve(version, renditions=None).renditions is None

    @pytest.mark.parametrize('version_id', ['8c21f0d7e4a6', '8C21F0D7E4A6B913', '../../../../etc/x'])
    def test_identifier_that_is_no_short_hash_is_rejected(self, version_id: str) -> None:
        """Reject an identifier that is not 16 lower-case hexadecimal digits, since it names a storage directory.

        :param version_id: Identifier that is no hash cut to 16 hexadecimal digits.
        :type version_id: str
        """
        with pytest.raises(ValueError, match='page version id'):
            evolve(make_page_version(page_id=PageId(uuid4())), id=PageVersionId(version_id))


class TestStep:
    """Tests for Step, which a recipe and the parameters of a job store as JSON."""

    @pytest.mark.parametrize('enabled', [True, False], ids=['on', 'off'])
    def test_step_reads_back_from_its_json(self, *, enabled: bool) -> None:
        """Verify a step, with its parameters and whether it is switched on, survives a round trip through JSON.

        :param enabled: Whether the step is on.
        :type enabled: bool
        """
        step = Step(processor_key=DESKEW_KEY, params={'max_angle': 5.0}, enabled=enabled)
        assert Step.from_map(json.loads(json.dumps(step.to_map()))) == step

    def test_step_stored_before_the_switch_existed_is_on(self) -> None:
        """Verify a recipe saved without the ``enabled`` key still runs every step, as it did when it was saved."""
        stored: dict[str, Any] = {StepField.PROCESSOR_KEY: DESKEW_KEY, StepField.PARAMS: {}}
        assert Step.from_map(stored).enabled is True

    def test_step_stored_before_the_identifier_and_the_condition_existed_gets_both(self) -> None:
        """Verify an old object gets a new identifier, and processes every page as it did."""
        stored: dict[str, Any] = {StepField.PROCESSOR_KEY: DESKEW_KEY, StepField.PARAMS: {}}
        first, second = Step.from_map(stored), Step.from_map(stored)
        expect(first.applies_to is AppliesTo.ALL)
        expect(first.step_id != second.step_id)
        assert_expectations()

    def test_identifier_and_condition_survive_a_copy_of_the_step(self) -> None:
        """Verify a step moved or copied with ``evolve`` keeps its identifier, and a new step gets its own."""
        step = Step(processor_key=DESKEW_KEY, applies_to=AppliesTo.PICTURES)
        expect(evolve(step, enabled=False).step_id == step.step_id)
        expect(Step(processor_key=DESKEW_KEY).step_id != step.step_id)
        assert_expectations()

    def test_object_without_a_processor_key_is_refused(self) -> None:
        """Reject an object that names no processor, which the callers that read job parameters turn into an error."""
        with pytest.raises(KeyError):
            Step.from_map({StepField.PARAMS: {}})


class TestAppliesTo:
    """Tests for the condition of a step, which decides what a page is."""

    @pytest.mark.parametrize(
        ('kind', 'color_mode', 'matching'),
        [
            (PageKind.TEXT, ColorMode.GRAY, {AppliesTo.ALL, AppliesTo.TEXT}),
            (PageKind.COVER, ColorMode.COLOR, {AppliesTo.ALL, AppliesTo.TEXT}),
            (PageKind.PLATE, ColorMode.COLOR, {AppliesTo.ALL, AppliesTo.PICTURES, AppliesTo.COLOR_PICTURES}),
            (PageKind.PLATE, ColorMode.GRAY, {AppliesTo.ALL, AppliesTo.PICTURES, AppliesTo.BW_PICTURES}),
            (PageKind.FRONTISPIECE, ColorMode.BILEVEL, {AppliesTo.ALL, AppliesTo.PICTURES, AppliesTo.BW_PICTURES}),
            (PageKind.PLATE, ColorMode.UNKNOWN, {AppliesTo.ALL, AppliesTo.PICTURES, AppliesTo.COLOR_PICTURES}),
        ],
        ids=['text', 'cover', 'colour-plate', 'gray-plate', 'bilevel-frontispiece', 'unknown-plate'],
    )
    def test_condition_matches_the_page_by_its_kind_and_its_colour(
        self, kind: PageKind, color_mode: ColorMode, matching: set[AppliesTo]
    ) -> None:
        """Verify which of the conditions meet a page of a kind and a colour mode.

        :param kind: Role of the page.
        :type kind: PageKind
        :param color_mode: Colour mode of the image the stage starts from.
        :type color_mode: ColorMode
        :param matching: The conditions the page meets.
        :type matching: set[AppliesTo]
        """
        assert {condition for condition in AppliesTo if condition.matches(kind, color_mode)} == matching


class TestRecipe:
    """Tests for Recipe."""

    ON: Step = Step(processor_key='geometry.on')
    OFF: Step = Step(processor_key='geometry.off', enabled=False)

    @staticmethod
    def recipe_of(*steps: Step) -> Recipe:
        """Build a recipe of the given steps.

        :param steps: The steps in the order they run.
        :type steps: Step
        :returns: The recipe.
        :rtype: Recipe
        """
        return evolve(make_recipe(project_id=make_project(owner_id=new_account_id()).id), steps=steps)

    def test_enabled_steps_are_the_steps_that_are_on_in_their_order(self) -> None:
        """Verify the steps a run takes are the ones switched on, in the order of the recipe."""
        later = Step(processor_key='geometry.later')
        assert self.recipe_of(self.ON, self.OFF, later).enabled_steps == (self.ON, later)

    def test_steps_through_one_keep_their_index_in_the_recipe_and_leave_out_the_ones_that_are_off(self) -> None:
        """Verify the pairs of a run through a step skip a step that is off, and name the index of each in the recipe."""
        recipe = self.recipe_of(self.ON, self.OFF, self.ON)
        expect(recipe.indexed_steps_through(None) == ((0, self.ON), (2, self.ON)))
        expect(recipe.indexed_steps_through(1) == ((0, self.ON),))
        expect(recipe.indexed_steps_through(9) == ((0, self.ON), (2, self.ON)))
        expect(self.recipe_of(self.OFF, self.ON).indexed_steps_through(0) == ())
        assert_expectations()

    def test_a_run_stops_short_only_when_a_step_that_is_on_is_left(self) -> None:
        """Verify a run through the last step that is on, or past it, stops nowhere, and one making no step too."""
        recipe = self.recipe_of(self.OFF, self.ON, self.OFF, self.ON, self.OFF)
        expect([recipe.stopped_at(through) for through in (None, 0, 1, 2, 3, 4)] == [None, None, 1, 1, None, None])
        assert_expectations()


class TestVersionInputsIdentify:
    """Tests for VersionInputs.identify()."""

    def test_equal_work_gets_the_same_identifier_of_16_hexadecimal_digits(self) -> None:
        """Verify a repeated step gets the identifier of its first run, which is the cache key of its result."""
        page_id = PageId(uuid4())
        first = VersionInputs(page_id=page_id, processor=SPLIT_NONE).identify()
        again = VersionInputs(page_id=page_id, processor=SPLIT_NONE, params={}).identify()
        assert (first == again, re.fullmatch(VERSION_ID_PATTERN, first) is not None) == (True, True)

    @pytest.mark.parametrize(
        'changed',
        [
            {'page_id': PageId(uuid4())},
            {'processor': ProcessorRef(key='split.none', version='2')},
            {'processor': ProcessorRef(key='split.spread', version='1')},
            {'params': {'angle': 0.8}},
            {'input_id': PageVersionId('0123456789abcdef')},
            {'edit_hash': '0123456789abcdef'},
            {'scale': VersionScale.PREVIEW},
            {'side': PageSide.RIGHT},
            {'skipped': True},
        ],
        ids=['page', 'processor-version', 'processor-key', 'params', 'input', 'edit', 'scale', 'side', 'skipped'],
    )
    def test_any_change_of_what_produced_the_version_changes_its_identifier(self, changed: dict[str, Any]) -> None:
        """Verify each ingredient of the hash moves the identifier, so different work never shares a directory.

        :param changed: Keyword arguments that replace one ingredient of the reference call.
        :type changed: dict[str, Any]
        """
        reference: dict[str, Any] = {'page_id': PAGE_ID, 'processor': SPLIT_NONE}
        assert VersionInputs(**reference).identify() != VersionInputs(**{**reference, **changed}).identify()

    def test_the_two_sides_of_the_book_are_different_work(self) -> None:
        """Verify a page of a step that reads its side gets another identifier on the other side of the book."""
        left = VersionInputs(page_id=PAGE_ID, processor=SPLIT_NONE, side=PageSide.LEFT).identify()
        right = VersionInputs(page_id=PAGE_ID, processor=SPLIT_NONE, side=PageSide.RIGHT).identify()
        assert left != right

    def test_order_of_parameters_does_not_matter(self) -> None:
        """Verify the same parameters written in another order are the same work."""
        first = VersionInputs(page_id=PAGE_ID, processor=SPLIT_NONE, params={'a': 1, 'b': 2}).identify()
        second = VersionInputs(page_id=PAGE_ID, processor=SPLIT_NONE, params={'b': 2, 'a': 1}).identify()
        assert first == second

    def test_full_run_without_an_edit_keeps_the_identifier_it_had_before_edits_existed(self) -> None:
        """Verify the identifier of a base version stays the hash of the four older ingredients."""
        digest = hashlib.sha256(
            json.dumps([str(PAGE_ID), 'split.none', '1', {}, None], sort_keys=True, separators=(',', ':')).encode()
        )
        assert VersionInputs(page_id=PAGE_ID, processor=SPLIT_NONE).identify() == digest.hexdigest()[:16]


class TestPageEditHashOf:
    """Tests for PageEdit.hash_of()."""

    def test_equal_edits_have_equal_hashes_of_16_digits(self) -> None:
        """Verify the same line and the same mask hash alike, so an old edit finds its old version again."""
        line = Line(start=Point(x=1100, y=0), end=Point(x=1104, y=1561))
        first = PageEdit.hash_of(line, 'a' * 64)
        again = PageEdit.hash_of(Line(start=Point(x=1100, y=0), end=Point(x=1104, y=1561)), 'a' * 64)
        assert (first == again, re.fullmatch(VERSION_ID_PATTERN, first) is not None) == (True, True)

    @pytest.mark.parametrize(
        ('geometry', 'mask_sha256'),
        [(Rotation(degrees=1.5), None), (None, 'b' * 64), (Rotation(degrees=1.0), 'b' * 64)],
        ids=['other-angle', 'mask-only', 'angle-and-mask'],
    )
    def test_any_change_of_the_edit_changes_its_hash(self, geometry: Rotation | None, mask_sha256: str | None) -> None:
        """Verify the shape and the mask each move the hash.

        :param geometry: Shape of the edit under test.
        :type geometry: Rotation | None
        :param mask_sha256: Digest of the mask of the edit under test.
        :type mask_sha256: str | None
        """
        assert PageEdit.hash_of(geometry, mask_sha256) != PageEdit.hash_of(Rotation(degrees=1.0), None)


class TestPageStepState:
    """Tests for the settings and the edit a page keeps for a step."""

    def test_settings_are_laid_over_the_parameters_and_the_rest_stays(self) -> None:
        """Verify a field the page changes wins, a field it does not change is the recipe's, and nothing is mutated."""
        recipe_params = {'max_angle': 5, 'min_confidence': 0.3}
        state = make_page_step_state(page_id=PAGE_ID, params={'max_angle': 3})
        assert (state.apply_to(recipe_params), recipe_params) == (
            {'max_angle': 3, 'min_confidence': 0.3},
            {'max_angle': 5, 'min_confidence': 0.3},
        )

    def test_a_state_with_no_setting_and_no_edit_is_empty(self) -> None:
        """Verify a state is empty only while it holds neither of its two layers."""
        edit = make_page_edit(page_id=PAGE_ID)
        states = [
            make_page_step_state(page_id=PAGE_ID),
            make_page_step_state(page_id=PAGE_ID, params={'max_angle': 3}),
            make_page_step_state(page_id=PAGE_ID, edit=edit),
        ]
        assert [state.is_empty for state in states] == [True, False, False]

    def test_key_names_the_page_the_stage_and_the_step(self) -> None:
        """Verify the key a repository stores the state under is the one of its page, stage and step."""
        state = make_page_step_state(page_id=PAGE_ID)
        assert (state.key.page_id, state.key.stage, state.key.step_id) == (PAGE_ID, state.stage, state.step_id)

    def test_a_layer_reads_back_what_it_was_written_from(self) -> None:
        """Verify the settings and the edit survive ``layer`` and ``with_layer``, and an empty layer is None."""
        edit = make_page_edit(page_id=PAGE_ID)
        state = make_page_step_state(page_id=PAGE_ID, params={'max_angle': 3}, edit=edit)
        empty = make_page_step_state(page_id=PAGE_ID)
        rebuilt = empty.with_layer(StepLayer.SETTINGS, state.layer(StepLayer.SETTINGS), EPOCH)
        rebuilt = rebuilt.with_layer(StepLayer.HAND, state.layer(StepLayer.HAND), EPOCH)
        expect(rebuilt == state)
        expect((empty.layer(StepLayer.SETTINGS), empty.layer(StepLayer.HAND)) == (None, None))
        expect(state.with_layer(StepLayer.SETTINGS, None, EPOCH).params == {})
        expect(state.with_layer(StepLayer.HAND, None, EPOCH).edit is None)
        assert_expectations()

    def test_the_layer_of_what_the_run_found_is_not_kept(self) -> None:
        """Verify reading or writing the found layer is a conflict, since no state keeps it yet."""
        state = make_page_step_state(page_id=PAGE_ID)
        with pytest.raises(ConflictError):
            state.layer(StepLayer.FOUND)
        with pytest.raises(ConflictError):
            state.with_layer(StepLayer.FOUND, None, EPOCH)


class TestPageEditSnapshot:
    """Tests for the snapshot of an edit that the history keeps."""

    def test_snapshot_is_json_and_rebuilds_the_edit(self) -> None:
        """Verify the snapshot holds JSON types only, and the edit it rebuilds has the shape, the hash and the mask."""
        edit = evolve(make_page_edit(page_id=PAGE_ID), mask_key=StorageKey('edits/mask.png'))
        snapshot = edit.to_snapshot()
        rebuilt = PageEdit.from_snapshot(edit.key, snapshot, edit.updated_at)
        expect(snapshot == json.loads(json.dumps(snapshot)))
        expect(rebuilt == edit)
        assert_expectations()

    def test_the_time_of_the_save_is_not_part_of_the_snapshot(self) -> None:
        """Verify two saves of one shape at different times give the same snapshot."""
        edit = make_page_edit(page_id=PAGE_ID)
        assert edit.to_snapshot() == evolve(edit, updated_at=EPOCH + timedelta(days=1)).to_snapshot()


class TestPageStepChangeBetween:
    """Tests for PageStepChange.between."""

    def test_the_change_holds_the_layer_before_and_after_and_the_time_of_the_new_state(self) -> None:
        """Verify the change names the page, the step and the layer, and carries the content on both sides."""
        before = make_page_step_state(page_id=PAGE_ID, params={'max_angle': 3})
        after = evolve(make_page_step_state(page_id=PAGE_ID), updated_at=EPOCH + timedelta(days=1))
        change = PageStepChange.between(before, after, StepLayer.SETTINGS, ChangeSource.USER)
        expect((change.page_id, change.step_id, change.key) == (PAGE_ID, after.step_id, after.key))
        expect((change.before, change.after) == ({'max_angle': 3}, None))
        expect((change.source, change.batch_id, change.undoes) == (ChangeSource.USER, None, None))
        expect(change.created_at == after.updated_at)
        assert_expectations()


class TestStepHistory:
    """Tests for the stack of changes that can be taken back."""

    @staticmethod
    def _change(source: ChangeSource = ChangeSource.USER, undoes: PageStepChange | None = None) -> PageStepChange:
        """Build a change of the settings of the deskew step.

        :param source: What made the change.
        :type source: ChangeSource
        :param undoes: The change it takes back, or None.
        :type undoes: PageStepChange | None
        :returns: The change.
        :rtype: PageStepChange
        """
        return evolve(
            make_page_step_change(page_id=PAGE_ID), source=source, undoes=None if undoes is None else undoes.id
        )

    def test_the_changes_an_undo_names_and_the_undos_do_not_stand(self) -> None:
        """Verify only a change that is no undo and that no undo names stands, oldest first."""
        first, second, third = (self._change() for _ in range(3))
        undo = self._change(ChangeSource.UNDO, second)
        history = StepHistory((first, second, undo, third))
        expect(history.standing == (first, third))
        expect(history.undone == {second.id})
        assert_expectations()

    def test_back_to_lists_the_change_and_the_later_ones_newest_first(self) -> None:
        """Verify an undo back to a change takes it and every standing change after it."""
        first, second, third = (self._change() for _ in range(3))
        assert StepHistory((first, second, third)).back_to(second.id) == (third, second)

    def test_back_to_a_change_that_does_not_stand_is_not_found(self) -> None:
        """Verify a change that was taken back, an undo and an unknown change cannot be taken back."""
        taken = self._change()
        undo = self._change(ChangeSource.UNDO, taken)
        history = StepHistory((taken, undo))
        for gone in (taken.id, undo.id, PageStepChangeId(uuid4())):
            with pytest.raises(NotFoundError):
                history.back_to(gone)


class TestBookDetails:
    """Tests for BookDetails invariants."""

    def test_empty_title_is_rejected(self) -> None:
        """Verify a book cannot exist without a title."""
        with pytest.raises(ValueError, match='title'):
            BookDetails(title='')


class TestProgress:
    """Tests for Progress.fraction."""

    @pytest.mark.parametrize(('done', 'total', 'fraction'), [(0, 0, 0.0), (1, 4, 0.25), (4, 4, 1.0)])
    def test_fraction(self, done: int, total: int, fraction: float) -> None:
        """Verify the fraction is done over total, and zero while the total is unknown.

        :param done: Steps completed.
        :type done: int
        :param total: Steps in all, zero while unknown.
        :type total: int
        :param fraction: Fraction the progress must report.
        :type fraction: float
        """
        assert Progress(done=done, total=total).fraction == fraction


class TestRenditions:
    """Tests for the format of the ``full`` image that Renditions records."""

    def test_full_defaults_to_jpeg(self) -> None:
        """Verify renditions written before the format was recorded read as JPEG."""
        assert Renditions().full is Rendition.FULL_JPEG

    @pytest.mark.parametrize('full', [Rendition.PREVIEW, Rendition.THUMBNAIL, Rendition.TILES])
    def test_full_that_is_not_a_full_format_is_rejected(self, full: Rendition) -> None:
        """Reject a ``full`` that names a preview, a thumbnail or a pyramid, which no image of full size can be.

        :param full: Rendition that is not a format of the ``full`` image.
        :type full: Rendition
        """
        with pytest.raises(ValueError, match='full'):
            Renditions(full=full)
