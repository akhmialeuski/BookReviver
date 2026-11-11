"""Tests for the value of a run of a stage: its last step, its mode, and the count of the pages the mode takes work from."""

import pytest

from bookreviver.domain.jobs import StageRun
from bookreviver.domain.runs import RunImpact, RunMode
from bookreviver.domain.stages import Stage
from tests.harness.builders.domain import new_book_id

BOOK_ID = new_book_id()
# The field of a run that names its last step, which the refusal of a wrong one names
THROUGH_STEP_FIELD: str = 'through_step'


class TestStageRun:
    """Tests for the parameters of a ``run-stage`` job."""

    def test_a_run_through_a_step_before_the_first_is_rejected(self) -> None:
        """Reject a negative index of the last step."""
        with pytest.raises(ValueError, match=THROUGH_STEP_FIELD):
            StageRun(stage=Stage.GEOMETRY, through_step=-1)


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
