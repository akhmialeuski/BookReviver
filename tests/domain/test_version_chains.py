"""Tests for the rules over the chains of versions a page keeps."""

from uuid import uuid4

from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import Stage
from bookreviver.domain.ids import PageId
from bookreviver.domain.version_chains import stage_depths
from tests.helpers.builders import make_page_version


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
