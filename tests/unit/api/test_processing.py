"""Tests for the request bodies of the processing endpoints: the form of an edit and the body of a run of a stage."""

import json

import pytest
from delayed_assert import assert_expectations, expect
from pydantic import ValidationError

from bookreviver.api.schemas.edits import EditForm
from bookreviver.api.schemas.processing import (
    StageRunBody,
)
from bookreviver.domain.geometry import ContentBox, EditorKind, Line, Mesh, SplitChoice
from bookreviver.domain.jobs import StageRun
from bookreviver.domain.runs import RunMode
from bookreviver.domain.stages import Stage


class TestEditForm:
    """Tests for the form of an edit, for the shapes the split, the mesh and the content box editors draw."""

    def test_a_choice_of_one_page_or_two_is_read_into_its_shape(self) -> None:
        """Verify the form of the split editor gives the choice, with the line of a cut when there is one."""
        line = {'start': {'x': 800.0, 'y': 0.0}, 'end': {'x': 790.0, 'y': 1200.0}}
        plain = EditForm.model_validate({'kind': EditorKind.SPLIT, 'geometry': '{"pages": 2}'}).to_edit()
        drawn = EditForm.model_validate(
            {'kind': EditorKind.SPLIT, 'geometry': json.dumps({'pages': 2, 'line': line})}
        ).to_edit()
        expect(plain.geometry == SplitChoice(pages=SplitChoice.TWO_PAGES))
        expect(isinstance(drawn.geometry, SplitChoice) and drawn.geometry.line == Line.from_data(line))
        assert_expectations()

    def test_the_curves_of_the_mesh_editor_are_read_into_a_mesh(self) -> None:
        """Verify the form of the mesh editor gives a mesh of the rows of nodes it was sent."""
        rows = [[{'x': 0.0, 'y': 100.0}, {'x': 900.0, 'y': 140.0}], [{'x': 0.0, 'y': 800.0}, {'x': 900.0, 'y': 850.0}]]
        edit = EditForm.model_validate({'kind': EditorKind.MESH, 'geometry': json.dumps({'rows': rows})}).to_edit()
        assert edit.geometry == Mesh.from_data({'rows': rows})

    @pytest.mark.parametrize(
        'geometry',
        [
            '{"rows": []}',
            '{"rows": [[{"x": 0, "y": 1}, {"x": 1, "y": 1}]]}',
            '{"pages": 2}',
            '{"rows": [[1, 2], [3, 4]]}',
        ],
        ids=['no-rows', 'one-row', 'a-split-choice', 'nodes-that-are-no-points'],
    )
    def test_curves_that_do_not_fit_are_refused(self, geometry: str) -> None:
        """Reject no rows, a single row, the shape of another editor, and nodes that are not points.

        :param geometry: The shape under test, as the form sends it.
        :type geometry: str
        """
        with pytest.raises(ValidationError):
            EditForm.model_validate({'kind': EditorKind.MESH, 'geometry': geometry})

    @pytest.mark.parametrize('geometry', ['{"pages": 3}', '{"line": null}', '{"pages": 1, "line": {"start": 1}}'])
    def test_a_choice_that_does_not_fit_is_refused(self, geometry: str) -> None:
        """Reject a number of pages other than one or two, a choice with no pages, and a line with no points.

        :param geometry: The shape under test, as the form sends it.
        :type geometry: str
        """
        with pytest.raises(ValidationError):
            EditForm.model_validate({'kind': EditorKind.SPLIT, 'geometry': geometry})

    def test_a_box_of_the_content_is_read_into_its_own_shape(self) -> None:
        """Verify the form of the content box editor gives a content box, which is no frame of the crop."""
        box = {'left': 10.0, 'top': 20.0, 'width': 300.0, 'height': 400.0}
        edit = EditForm.model_validate({'kind': EditorKind.CONTENT_BOX, 'geometry': json.dumps(box)}).to_edit()
        expect(edit.geometry == ContentBox.from_data(box))
        expect(isinstance(edit.geometry, ContentBox) and edit.geometry.editor is EditorKind.CONTENT_BOX)
        assert_expectations()

    @pytest.mark.parametrize(
        'geometry',
        ['{"left": 0, "top": 0, "width": 0, "height": 5}', '{"pages": 2}', '{"left": 1}'],
        ids=['no-width', 'a-split-choice', 'a-corner-only'],
    )
    def test_a_box_of_the_content_that_does_not_fit_is_refused(self, geometry: str) -> None:
        """Reject a box with no area, the shape of another editor, and a box with a side missing.

        :param geometry: The shape under test, as the form sends it.
        :type geometry: str
        """
        with pytest.raises(ValidationError):
            EditForm.model_validate({'kind': EditorKind.CONTENT_BOX, 'geometry': geometry})


class TestStageRunBody:
    """Tests for the body of a run of a stage."""

    @pytest.mark.parametrize('confirmed', [True, False], ids=['confirmed', 'not-confirmed'])
    def test_the_confirmation_of_an_unsplit_reaches_the_run_and_the_job_that_stores_it(
        self, *, confirmed: bool
    ) -> None:
        """Verify the confirmation is in the run the body states, and survives being stored as the parameters of a job.

        :param confirmed: Whether the body confirms that undoing a split deletes the right half.
        :type confirmed: bool
        """
        run = StageRunBody(confirm_unsplit=confirmed).to_run(Stage.PAGE_SPLIT)
        assert (run.confirm_unsplit, StageRun.from_map(run.to_map()).confirm_unsplit) == (confirmed, confirmed)

    @pytest.mark.parametrize('mode', list(RunMode), ids=[mode.value for mode in RunMode])
    def test_the_mode_and_its_confirmation_reach_the_run_and_the_job_that_stores_it(self, mode: RunMode) -> None:
        """Verify the mode and the confirmation to overwrite are in the run the body states, and survive the job.

        :param mode: Mode under test.
        :type mode: RunMode
        """
        run = StageRunBody(mode=mode, confirm_overwrite=True).to_run(Stage.GEOMETRY)
        stored = StageRun.from_map(run.to_map())
        assert (run.mode, stored.mode, stored.confirm_overwrite) == (mode, mode, True)

    def test_a_job_stored_before_the_modes_runs_in_the_usual_mode(self) -> None:
        """Verify the parameters of a job without a mode are read as a run that keeps the work of the pages."""
        old = {key: value for key, value in StageRun(stage=Stage.GEOMETRY).to_map().items() if key != 'mode'}
        assert StageRun.from_map(old).mode is RunMode.KEEP
