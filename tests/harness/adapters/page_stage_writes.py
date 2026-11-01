"""A record of how the page stage repository is written, for the tests that prove records go in one batch.

The record wraps the methods by hand instead of with ``unittest.mock.patch.object(..., autospec=True)``, because the
autospec of Python 3.14.3 fails on annotations imported only for type checking.
"""

from typing import TYPE_CHECKING

from bookreviver.adapters.persistence.sqlalchemy.repositories import SqlAlchemyPageStageRepository

if TYPE_CHECKING:
    from collections.abc import Sequence

    import pytest

    from bookreviver.domain.stages import PageStage


class PageStageWrites:
    """Records every write of a page stage repository after ``start``, and lets it go on as it did.

    ``KeyedRepository.save`` stores through ``save_many``, so a record written alone is in ``saves`` and also in a
    batch of one, and a test of a batch asserts both lists.

    :ivar saves: The records written by a call of ``save``, in call order.
    :ivar batches: The records of each call of ``save_many``, in call order, a call that has none included.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Hold the fixture that restores the repository when the test ends.

        :param monkeypatch: Fixture that undoes the patch of the repository after the test.
        :type monkeypatch: pytest.MonkeyPatch
        """
        self._monkeypatch = monkeypatch
        self.saves: list[PageStage] = []
        self.batches: list[list[PageStage]] = []

    def start(self) -> None:
        """Record every write from now on, so what a test seeds before the call is not in the lists."""
        save = SqlAlchemyPageStageRepository.save
        save_many = SqlAlchemyPageStageRepository.save_many

        def recording_save(repository: SqlAlchemyPageStageRepository, entity: PageStage) -> PageStage:
            """Record the record, then store it as the repository does.

            :param repository: The repository of the transaction that writes.
            :type repository: SqlAlchemyPageStageRepository
            :param entity: Record to store.
            :type entity: PageStage
            :returns: The record as stored.
            :rtype: PageStage
            """
            self.saves.append(entity)
            return save(repository, entity)

        def recording_save_many(
            repository: SqlAlchemyPageStageRepository, entities: Sequence[PageStage]
        ) -> Sequence[PageStage]:
            """Record the batch, then store it as the repository does.

            :param repository: The repository of the transaction that writes.
            :type repository: SqlAlchemyPageStageRepository
            :param entities: Records to store.
            :type entities: Sequence[PageStage]
            :returns: The records as stored.
            :rtype: Sequence[PageStage]
            """
            self.batches.append(list(entities))
            return save_many(repository, entities)

        self._monkeypatch.setattr(SqlAlchemyPageStageRepository, 'save', recording_save)
        self._monkeypatch.setattr(SqlAlchemyPageStageRepository, 'save_many', recording_save_many)
