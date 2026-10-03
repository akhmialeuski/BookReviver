"""Tests for the domain entities and values."""

import hashlib
import json
import re
from typing import Any
from uuid import uuid4

import pytest
from attrs import evolve

from bookreviver.domain.entities import VERSION_ID_PATTERN, PageEdit, VersionInputs
from bookreviver.domain.enums import JobKind, PageOrigin, PageSide, Rendition, Stage, StepField, VersionScale
from bookreviver.domain.geometry import Line, Point, Rotation
from bookreviver.domain.ids import PageId, PageVersionId, ScanId
from bookreviver.domain.values import BookDetails, ProcessorRef, Progress, Renditions, StageRun, Step
from tests.helpers.builders import (
    SPLIT_NONE,
    make_job,
    make_page,
    make_page_version,
    make_project,
    make_recipe,
    new_account_id,
)

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

    def test_object_without_a_processor_key_is_refused(self) -> None:
        """Reject an object that names no processor, which the callers that read job parameters turn into an error."""
        with pytest.raises(KeyError):
            Step.from_map({StepField.PARAMS: {}})


class TestRecipe:
    """Tests for Recipe."""

    def test_enabled_steps_are_the_steps_that_are_on_in_their_order(self) -> None:
        """Verify the steps a run takes are the ones switched on, in the order of the recipe."""
        recipe = make_recipe(project_id=make_project(owner_id=new_account_id()).id)
        first, third = Step(processor_key='geometry.first'), Step(processor_key='geometry.third')
        second = Step(processor_key='geometry.second', enabled=False)
        assert evolve(recipe, steps=(first, second, third)).enabled_steps == (first, third)


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
        ],
        ids=['page', 'processor-version', 'processor-key', 'params', 'input', 'edit', 'scale', 'side'],
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
