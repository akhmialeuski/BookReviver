"""Tests for the catalogue of processors found through entry points."""

import logging
from importlib.metadata import EntryPoint
from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.app.plugins import PROCESSOR_GROUP, EntryPointCatalog
from bookreviver.domain.enums import WorkerPool
from bookreviver.domain.errors import NotFoundError
from bookreviver.plugins.base import ModelProcessor
from tests.helpers.samples import CV_MISSING

if TYPE_CHECKING:
    from collections.abc import Collection

FAKE_MODULE: str = 'tests.helpers.processors'


def entry_point(name: str, target: str) -> EntryPoint:
    """Build the entry point of a processor in the group of the processors.

    :param name: Name the processor is registered under.
    :type name: str
    :param target: Reference to the class, as ``module:attr``.
    :type target: str
    :returns: The entry point, not yet loaded.
    :rtype: EntryPoint
    """
    return EntryPoint(name=name, value=target, group=PROCESSOR_GROUP)


class TestEntryPointCatalog:
    """Tests for EntryPointCatalog."""

    def test_built_in_processors_are_found_through_their_entry_points(self) -> None:
        """Verify the catalogue lists the processors the project's own metadata registers, by key."""
        keys = [spec.key for spec in EntryPointCatalog(pools=set(WorkerPool)).specs()]
        assert {'split.none', 'pages.blank'} <= set(keys)

    def test_opencv_processors_are_found_through_their_entry_points(self) -> None:
        """Verify the processors that need OpenCV are in the catalogue where the group ``cv`` is installed."""
        pytest.importorskip('cv2', reason=CV_MISSING)
        keys = [spec.key for spec in EntryPointCatalog(pools=set(WorkerPool)).specs()]
        assert {'split.spread', 'split.auto', 'geometry.deskew'} <= set(keys)

    def test_every_parameter_of_a_built_in_processor_has_a_title_and_a_description_of_its_own(self) -> None:
        """Verify the form of a processor has a label and a hint for each field, not the name of the field.

        Pydantic fills a title in from the name of the field, such as ``Overlap Px``, so the JSON Schema alone cannot
        tell a title written for the form from one made of the name. The check reads the model, where a title that
        nobody wrote is None. The processors that need OpenCV are checked where it is installed.
        """
        catalog = EntryPointCatalog(pools=set(WorkerPool))
        unlabelled = [
            f'{spec.key}: {name}'
            for spec in catalog.specs()
            if isinstance(processor := catalog.get(spec.key), ModelProcessor)
            for name, field in processor.params_model.model_fields.items()
            if not field.title or not field.description
        ]
        assert unlabelled == []

    def test_specs_are_listed_by_key(self) -> None:
        """Verify the specs come ordered by key, whatever order the entry points were found in."""
        registered = [
            entry_point('recognition.fake', f'{FAKE_MODULE}:GpuProcessor'),
            entry_point('geometry.fake', f'{FAKE_MODULE}:FakeProcessor'),
        ]
        catalog = EntryPointCatalog(pools=set(WorkerPool), registered=registered)
        assert [spec.key for spec in catalog.specs()] == ['geometry.fake', 'recognition.fake']

    def test_processor_is_found_by_its_key(self) -> None:
        """Verify get returns the processor of the key, and an unknown key is a NotFoundError."""
        catalog = EntryPointCatalog(
            pools=set(WorkerPool), registered=[entry_point('geometry.fake', f'{FAKE_MODULE}:FakeProcessor')]
        )
        expect(catalog.get('geometry.fake').spec.key == 'geometry.fake')
        with pytest.raises(NotFoundError):
            catalog.get('geometry.missing')
        assert_expectations()

    @pytest.mark.parametrize(
        ('pools', 'expected'),
        [
            ({WorkerPool.CPU}, ['geometry.fake']),
            ({WorkerPool.GPU}, ['recognition.fake']),
            ({WorkerPool.LLM}, []),
        ],
        ids=['cpu', 'gpu', 'llm'],
    )
    def test_processors_of_pools_this_process_does_not_serve_are_left_out(
        self, pools: Collection[WorkerPool], expected: list[str]
    ) -> None:
        """Verify a worker of one pool offers the processors of its pool alone.

        :param pools: Pools the catalogue serves.
        :type pools: Collection[WorkerPool]
        :param expected: Keys the catalogue must list.
        :type expected: list[str]
        """
        registered = [
            entry_point('geometry.fake', f'{FAKE_MODULE}:FakeProcessor'),
            entry_point('recognition.fake', f'{FAKE_MODULE}:GpuProcessor'),
        ]
        catalog = EntryPointCatalog(pools=pools, registered=registered)
        assert [spec.key for spec in catalog.specs()] == expected

    def test_processor_whose_dependency_is_missing_is_left_out_with_a_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Verify a plugin that cannot be imported does not stop the application, and the log says which one.

        :param caplog: Capture of the log records of the test.
        :type caplog: pytest.LogCaptureFixture
        """
        registered = [
            entry_point('geometry.needs-cv', 'tests.helpers.not_installed:Processor'),
            entry_point('geometry.fake', f'{FAKE_MODULE}:FakeProcessor'),
        ]
        with caplog.at_level(logging.WARNING):
            catalog = EntryPointCatalog(pools=set(WorkerPool), registered=registered)
        expect([spec.key for spec in catalog.specs()] == ['geometry.fake'])
        expect('geometry.needs-cv' in caplog.text)
        assert_expectations()

    def test_processor_registered_under_another_name_is_rejected(self) -> None:
        """Reject a registration whose name is not the key of its processor, since a recipe addresses it by the name."""
        with pytest.raises(ValueError, match='registers the processor'):
            EntryPointCatalog(
                pools=set(WorkerPool), registered=[entry_point('geometry.other', f'{FAKE_MODULE}:FakeProcessor')]
            )
