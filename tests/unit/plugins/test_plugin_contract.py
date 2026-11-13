"""Tests for what every built-in plugin owes its contract, read through the entry points as the application reads them.

A plugin has one structure: a spec, a model of its parameters whose JSON Schema the spec carries, the half that
computes, and for a step that reads the whole book the half that reads it. The interface draws the settings of a step
from the spec alone, so what the spec says must be what the plugin does.
"""

from typing import TYPE_CHECKING

from bookreviver.domain.processors import ProcessorRole

if TYPE_CHECKING:
    from bookreviver.app.plugins import EntryPointCatalog

# The key of the JSON Schema of the parameters that lists the settings of a step
PROPERTIES: str = 'properties'


class TestPluginContract:
    """Tests for the structure every installed plugin shares."""

    def test_a_plugin_declares_fields_of_the_book_exactly_when_it_reads_the_book(
        self, fx_processors: EntryPointCatalog
    ) -> None:
        """Verify the spec names settings left to the book for a plugin with a half that reads it, and for no other."""
        mismatched = [
            spec.key
            for spec in fx_processors.specs()
            if bool(spec.book_fields) != (fx_processors.get(spec.key).book_step is not None)
        ]
        assert mismatched == []

    def test_the_fields_of_the_book_are_settings_of_the_step(self, fx_processors: EntryPointCatalog) -> None:
        """Verify every setting a spec leaves to the book is one the form of the step draws."""
        unknown = {
            spec.key: sorted(set(spec.book_fields) - set(spec.parameters.get(PROPERTIES, {})))
            for spec in fx_processors.specs()
            if set(spec.book_fields) - set(spec.parameters.get(PROPERTIES, {}))
        }
        assert unknown == {}

    def test_a_field_of_the_book_is_left_to_the_book_while_it_is_zero(self, fx_processors: EntryPointCatalog) -> None:
        """Verify the half that reads the book takes the defaults of the step as leaving its fields to the book."""
        reading = [spec for spec in fx_processors.specs() if spec.book_fields]
        for spec in reading:
            processor = fx_processors.get(spec.key)
            defaults = processor.validate_params({})
            assert processor.book_step is not None
            assert processor.book_step.leaves_to_book(defaults)
            assert all(not defaults[name] for name in spec.book_fields)

    def test_the_key_of_a_plugin_starts_with_the_group_it_lies_in(self, fx_processors: EntryPointCatalog) -> None:
        """Verify a plugin lies in the package of the group its key names, so the layout and the keys agree."""
        misplaced = [
            spec.key
            for spec in fx_processors.specs()
            if type(fx_processors.get(spec.key)).__module__.split('.')[-2] != spec.key.split('.', maxsplit=1)[0]
        ]
        assert misplaced == []

    def test_every_base_role_is_filled_by_one_plugin(self, fx_processors: EntryPointCatalog) -> None:
        """Verify each role the application asks for by name has exactly one plugin, so the answer is never a choice."""
        roles = [spec.role for spec in fx_processors.specs() if spec.role is not ProcessorRole.STEP]
        assert sorted(roles) == sorted(set(roles))
        assert fx_processors.missing_base_roles() == []
