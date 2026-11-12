"""Tests for the catalogue of plugins, which finds the processor of a role the application asks for."""

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.processors import ProcessorRole
from bookreviver.plugins.pages.blank import BlankPage
from bookreviver.plugins.split.split_none import SplitNone
from tests.harness.fakes.processing import FakeCatalogue
from tests.harness.fakes.processors import FakeContentProbe, FakeProcessor


class TestOfRole:
    """Tests for ProcessorCatalog.of_role."""

    def test_the_processor_that_declares_the_role_is_found(self) -> None:
        """Verify each role names the processor whose spec declares it, whatever its key is."""
        catalogue = FakeCatalogue([FakeProcessor(), SplitNone(), FakeContentProbe([])])
        expect(catalogue.of_role(ProcessorRole.SCAN_COPY) == SplitNone.spec)
        expect(catalogue.of_role(ProcessorRole.CONTENT_PROBE) == FakeContentProbe.spec)
        expect(catalogue.of_role(ProcessorRole.STEP) == FakeProcessor.spec)
        assert_expectations()

    def test_a_role_no_processor_declares_is_not_found(self) -> None:
        """Verify a catalogue without a processor of the role refuses, as a machine without that plugin does."""
        with pytest.raises(NotFoundError):
            FakeCatalogue([FakeProcessor()]).of_role(ProcessorRole.BLANK_LEAF)


class TestMissingBaseRoles:
    """Tests for ProcessorCatalog.missing_base_roles."""

    def test_a_catalogue_with_the_base_plugins_misses_nothing(self) -> None:
        """Verify the copy of a scan and the blank leaf are all the application cannot work without."""
        assert FakeCatalogue([SplitNone(), BlankPage(), FakeProcessor()]).missing_base_roles() == []

    def test_a_base_plugin_that_is_not_installed_is_named(self) -> None:
        """Verify each base role no processor fills is listed, and the probe of the content is not one of them."""
        expect(FakeCatalogue([SplitNone()]).missing_base_roles() == [ProcessorRole.BLANK_LEAF])
        expect(set(FakeCatalogue([FakeProcessor()]).missing_base_roles()) == ProcessorRole.base())
        assert_expectations()
