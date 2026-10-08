"""Tests for the value of a run of a stage: its last step, its mode, and the count of the pages the mode takes work from."""

import pytest

from bookreviver.domain.enums import RunMode, Stage
from bookreviver.domain.ids import PageId, PageVersionId
from bookreviver.domain.values import THROUGH_STEP_KEY, RunImpact, StageRun
from tests.helpers.builders import make_page, make_project, new_account_id

PROJECT_ID = make_project(owner_id=new_account_id()).id
THROUGH_THIRD_STEP: int = 2


class TestStageRun:
    """Tests for the parameters of a ``run-stage`` job."""

    def test_the_last_step_of_a_run_survives_the_job_parameters(self) -> None:
        """Verify a run through a step read back from the parameters of its job still stops there."""
        run = StageRun(stage=Stage.GEOMETRY, through_step=THROUGH_THIRD_STEP)
        assert StageRun.from_map(run.to_map()).through_step == run.through_step

    def test_a_job_stored_without_the_last_step_is_a_run_through_every_step(self) -> None:
        """Verify the parameters of a job queued before the field existed are read as a run through every step."""
        stored = {
            key: value for key, value in StageRun(stage=Stage.GEOMETRY).to_map().items() if key != THROUGH_STEP_KEY
        }
        assert StageRun.from_map(stored).through_step is None

    def test_a_run_through_a_step_before_the_first_is_rejected(self) -> None:
        """Reject a negative index of the last step."""
        with pytest.raises(ValueError, match=THROUGH_STEP_KEY):
            StageRun(stage=Stage.GEOMETRY, through_step=-1)

    def test_a_job_stored_with_a_recipe_and_a_pin_is_still_read(self) -> None:
        """Verify the keys a run had before a page was processed by the recipe of its kind are left unread."""
        stored = {**StageRun(stage=Stage.GEOMETRY).to_map(), 'recipe_id': None, 'pin': False}
        assert StageRun.from_map(stored) == StageRun(stage=Stage.GEOMETRY)


class TestRunMode:
    """Tests for the mode of a run and the count of the pages it takes work from."""

    @pytest.mark.parametrize(
        ('mode', 'affected'),
        [(RunMode.KEEP, 0), (RunMode.SKIP_OWN, 0), (RunMode.DROP_OWN, 7)],
        ids=['keep', 'skip-own', 'drop-own'],
    )
    def test_a_mode_takes_work_from_the_pages_it_names(self, mode: RunMode, affected: int) -> None:
        """Verify only the mode that drops the work of the pages takes it from the pages that have any.

        :param mode: Mode under test.
        :type mode: RunMode
        :param affected: How many pages lose work to it.
        :type affected: int
        """
        assert RunImpact(mode=mode, pages=9, own_pages=7).affected == affected

    def test_a_run_that_makes_a_version_again_keeps_the_work_of_the_page(self) -> None:
        """Reject a mode that takes work away on a run that makes a version again, which runs with what was stored."""
        with pytest.raises(ValueError, match='keeps the settings'):
            StageRun(
                stage=Stage.GEOMETRY,
                page_ids=(PageId(make_page(project_id=PROJECT_ID).id),),
                remake=PageVersionId('0123456789abcdef'),
                mode=RunMode.DROP_OWN,
            )
