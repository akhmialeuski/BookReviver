"""A record of which pages the page repository writes, for the tests that prove a change writes only the pages it moves.

A page written with the state it already had reads back the same, so only a record of the calls tells a needless write
from none.

The record wraps the methods by hand instead of with ``unittest.mock.patch.object(..., autospec=True)``, because the
autospec of Python 3.14.3 evaluates the annotations of the repository, whose ``Sequence`` is imported only for type
checking, and fails with ``NameError`` (https://github.com/python/cpython/issues/145754). Python 3.14.4 carries the
fix, and once the project requires it the wrappers give way to the autospec.
"""

from typing import TYPE_CHECKING

from bookreviver.adapters.persistence.sqlalchemy.repositories import SqlAlchemyPageRepository

if TYPE_CHECKING:
    from collections.abc import Sequence

    import pytest

    from bookreviver.domain.entities import Page
    from bookreviver.domain.ids import PageId


class PageWrites:
    """Records the pages every ``update`` and ``update_many`` of the page repository writes after ``start``.

    :ivar page_ids: Identifiers of the pages written since ``start``.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Hold the fixture that restores the repository when the test ends.

        :param monkeypatch: Fixture that undoes the patch of the repository after the test.
        :type monkeypatch: pytest.MonkeyPatch
        """
        self._monkeypatch = monkeypatch
        self.page_ids: set[PageId] = set()

    def start(self) -> None:
        """Record every write from now on, so the pages a test seeds before the call are not in the record."""
        update = SqlAlchemyPageRepository.update
        update_many = SqlAlchemyPageRepository.update_many

        def recording_update(repository: SqlAlchemyPageRepository, entity: Page) -> Page:
            """Record the page, then write it as the repository does.

            :param repository: The repository of the transaction that writes.
            :type repository: SqlAlchemyPageRepository
            :param entity: Page with its new state.
            :type entity: Page
            :returns: The page as stored.
            :rtype: Page
            """
            self.page_ids.add(entity.id)
            return update(repository, entity)

        def recording_update_many(repository: SqlAlchemyPageRepository, pages: Sequence[Page]) -> None:
            """Record the pages, then write them as the repository does.

            :param repository: The repository of the transaction that writes.
            :type repository: SqlAlchemyPageRepository
            :param pages: Pages with their new state.
            :type pages: Sequence[Page]
            """
            self.page_ids.update(page.id for page in pages)
            update_many(repository, pages)

        self._monkeypatch.setattr(SqlAlchemyPageRepository, 'update', recording_update)
        self._monkeypatch.setattr(SqlAlchemyPageRepository, 'update_many', recording_update_many)
