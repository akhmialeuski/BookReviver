"""Tests for the rules over the chains of versions a page keeps."""

from typing import TYPE_CHECKING
from uuid import uuid4

from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import Stage
from bookreviver.domain.ids import PageId, PageVersionId, ProjectId, StepId
from bookreviver.domain.values import ProcessorRef, Step
from bookreviver.domain.version_chains import StepVersions, stage_depths, step_places, versions_of_step
from tests.helpers.builders import make_page_version, make_recipe

if TYPE_CHECKING:
    from bookreviver.domain.entities import PageVersion

DESKEW_KEY: str = 'geometry.deskew'
CROP_KEY: str = 'geometry.crop'
NORMALIZE_KEY: str = 'geometry.normalize'
CROP_STEP: StepId = StepId(uuid4())
CROP_PLACE: int = 1
NORMALIZE_PLACE: int = 2
UNKNOWN_VERSION: PageVersionId = PageVersionId('0000000000000000')


class TestStageDepths:
    """Tests for stage_depths."""

    def test_a_chain_of_one_stage_counts_the_versions_each_step_reads(self) -> None:
        """Verify the first step of a stage has depth zero, though it reads a version of an earlier stage."""
        page_id = PageId(uuid4())
        base = make_page_version(page_id=page_id)
        first = evolve(make_page_version(page_id=page_id, minutes=1), stage=Stage.GEOMETRY, input_id=base.id)
        second = evolve(make_page_version(page_id=page_id, minutes=2), stage=Stage.GEOMETRY, input_id=first.id)
        cleanup = evolve(make_page_version(page_id=page_id, minutes=3), stage=Stage.CLEANUP, input_id=second.id)
        depths = stage_depths([base, first, second, cleanup])
        expect([depths[version.id] for version in (base, first, second, cleanup)] == [0, 0, 1, 0])
        assert_expectations()

    def test_a_version_whose_input_is_not_listed_has_depth_zero(self) -> None:
        """Verify an input a collection deleted ends the chain instead of failing it."""
        page_id = PageId(uuid4())
        orphan = evolve(
            make_page_version(page_id=page_id), stage=Stage.GEOMETRY, input_id=make_page_version(page_id=page_id).id
        )
        assert stage_depths([orphan]) == {orphan.id: 0}


def made_by(page_id: PageId, key: str, stage: Stage, minutes: int, read: PageVersion | None) -> PageVersion:
    """Build a version a processor made, reading another version.

    :param page_id: Page the version belongs to.
    :type page_id: PageId
    :param key: Key of the processor that made it.
    :type key: str
    :param stage: Stage of the version.
    :type stage: Stage
    :param minutes: Minutes after the epoch the version was created.
    :type minutes: int
    :param read: The version it reads, or None.
    :type read: PageVersion | None
    :returns: The version.
    :rtype: PageVersion
    """
    return evolve(
        make_page_version(page_id=page_id, minutes=minutes),
        stage=stage,
        processor=ProcessorRef(key=key, version='1'),
        input_id=None if read is None else read.id,
    )


class TestStepPlaces:
    """Tests for step_places."""

    def test_a_step_copied_into_a_variant_has_a_place_in_each_recipe_that_has_it_on(self) -> None:
        """Verify one step identifier gives one place for each recipe, and none for a recipe that has it off."""
        project_id = ProjectId(uuid4())
        crop = Step(processor_key=CROP_KEY, step_id=CROP_STEP)
        first = evolve(make_recipe(project_id=project_id), steps=(Step(processor_key=DESKEW_KEY), crop))
        second = evolve(make_recipe(project_id=project_id), steps=(crop,))
        off = evolve(make_recipe(project_id=project_id), steps=(evolve(crop, enabled=False),))
        elsewhere = evolve(make_recipe(project_id=project_id), steps=(Step(processor_key=DESKEW_KEY),))
        places = step_places([first, second, off, elsewhere], CROP_STEP)
        assert places == {(CROP_KEY, CROP_PLACE), (CROP_KEY, 0)}


class TestVersionsOfStep:
    """Tests for versions_of_step."""

    def test_a_processor_at_the_place_of_the_step_is_the_step_without_the_versions_that_read_it(self) -> None:
        """Verify both runs of the middle step are its versions, and the versions that read them are not."""
        versions = TestStepVersions.book()
        _, _, crop, _, _, again = versions
        made = versions_of_step(versions, Stage.GEOMETRY, {(CROP_KEY, CROP_PLACE)}, stage_depths(versions))
        assert made == {crop.id, again.id}

    def test_a_processor_at_another_place_is_not_the_step(self) -> None:
        """Verify a version of the processor of the step that reads more versions than its place is left out."""
        versions = TestStepVersions.book()
        made = versions_of_step(versions, Stage.GEOMETRY, {(CROP_KEY, CROP_PLACE + 1)}, stage_depths(versions))
        assert made == frozenset()


class TestStepVersions:
    """Tests for StepVersions."""

    @staticmethod
    def book() -> list[PageVersion]:
        """Build the versions of a page: a base, a chain of three steps, a second run of the middle one, and a cleanup.

        :returns: The base, deskew, crop, normalize, cleanup and the second crop, in that order.
        :rtype: list[PageVersion]
        """
        page_id = PageId(uuid4())
        base = make_page_version(page_id=page_id)
        deskew = made_by(page_id, DESKEW_KEY, Stage.GEOMETRY, 1, base)
        crop = made_by(page_id, CROP_KEY, Stage.GEOMETRY, 2, deskew)
        normalize = made_by(page_id, NORMALIZE_KEY, Stage.GEOMETRY, 3, crop)
        cleanup = made_by(page_id, 'cleanup.binarize', Stage.CLEANUP, 4, normalize)
        again = made_by(page_id, CROP_KEY, Stage.GEOMETRY, 5, deskew)
        return [base, deskew, crop, normalize, cleanup, again]

    def test_a_step_has_the_versions_of_its_processor_at_its_place_and_those_that_read_them_go_with_them(self) -> None:
        """Verify both runs of the middle step are its versions, and the later versions of every stage go with them."""
        versions = self.book()
        _, _, crop, normalize, cleanup, again = versions
        chain = StepVersions.of(versions, Stage.GEOMETRY, {(CROP_KEY, CROP_PLACE)})
        expect(chain.made == {crop.id, again.id})
        expect(chain.doomed == {crop.id, again.id, normalize.id, cleanup.id})
        expect(chain.depths[normalize.id] == CROP_PLACE + 1)
        assert_expectations()

    def test_a_processor_at_another_place_is_not_the_step(self) -> None:
        """Verify a version of the processor of the step that reads more or fewer versions than its place is left out."""
        chain = StepVersions.of(self.book(), Stage.GEOMETRY, {(CROP_KEY, CROP_PLACE + 1)})
        expect(chain.made == frozenset())
        expect(chain.doomed == frozenset())
        assert_expectations()

    def test_the_page_stands_on_what_the_step_read_in_its_own_chain(self) -> None:
        """Verify the version a head goes back to is the input of the version of the step in the chain of the head."""
        versions = self.book()
        _, deskew, crop, normalize, _, again = versions
        chain = StepVersions.of(versions, Stage.GEOMETRY, {(CROP_KEY, CROP_PLACE)})
        expect(chain.input_of(normalize.id) == deskew.id)
        expect(chain.input_of(crop.id) == deskew.id)
        expect(chain.input_of(again.id) == deskew.id)
        assert_expectations()

    def test_the_first_step_leaves_the_page_standing_on_nothing_of_its_stage(self) -> None:
        """Verify what the first step read, which an earlier stage made, is no current version of the stage."""
        versions = self.book()
        _, deskew, crop, normalize, cleanup, again = versions
        chain = StepVersions.of(versions, Stage.GEOMETRY, {(DESKEW_KEY, 0)})
        expect(chain.doomed == {deskew.id, crop.id, normalize.id, cleanup.id, again.id})
        expect(chain.input_of(normalize.id) is None)
        assert_expectations()

    def test_a_head_that_the_step_did_not_make_has_no_input_to_stand_on(self) -> None:
        """Verify a version outside the chains of the step, and a version that is not listed, give none."""
        versions = self.book()
        chain = StepVersions.of(versions, Stage.GEOMETRY, {(NORMALIZE_KEY, NORMALIZE_PLACE)})
        expect(chain.input_of(versions[1].id) is None)
        expect(chain.input_of(UNKNOWN_VERSION) is None)
        assert_expectations()

    def test_the_versions_that_go_with_the_step_do_not_depend_on_the_order_of_the_list(self) -> None:
        """Verify a version listed before the version it reads is doomed as well, across two stages."""
        versions = self.book()
        _, _, crop, normalize, cleanup, again = versions
        chain = StepVersions.of(versions[::-1], Stage.GEOMETRY, {(CROP_KEY, CROP_PLACE)})
        expect(chain.made == {crop.id, again.id})
        expect(chain.doomed == {crop.id, again.id, normalize.id, cleanup.id})
        assert_expectations()
