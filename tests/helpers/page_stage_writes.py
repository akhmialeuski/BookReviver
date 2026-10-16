"""A record of how the in-memory page stage repository is written, for the tests that prove records go in one batch."""

from typing import TYPE_CHECKING

from bookreviver.adapters.persistence.memory.unit_of_work import InMemoryPageStageRepository

if TYPE_CHECKING:
    from collections.abc import Sequence

    import pytest

    from bookreviver.domain.entities import PageStage


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
        save = InMemoryPageStageRepository.save
        save_many = InMemoryPageStageRepository.save_many

        def recording_save(repository: InMemoryPageStageRepository, entity: PageStage) -> PageStage:
            """Record the record, then store it as the repository does.

            :param repository: The repository of the transaction that writes.
            :type repository: InMemoryPageStageRepository
            :param entity: Record to store.
            :type entity: PageStage
            :returns: The record as stored.
            :rtype: PageStage
            """
            self.saves.append(entity)
            return save(repository, entity)

        def recording_save_many(
            repository: InMemoryPageStageRepository, entities: Sequence[PageStage]
        ) -> Sequence[PageStage]:
            """Record the batch, then store it as the repository does.

            :param repository: The repository of the transaction that writes.
            :type repository: InMemoryPageStageRepository
            :param entities: Records to store.
            :type entities: Sequence[PageStage]
            :returns: The records as stored.
            :rtype: Sequence[PageStage]
            """
            self.batches.append(list(entities))
            return save_many(repository, entities)

        self._monkeypatch.setattr(InMemoryPageStageRepository, 'save', recording_save)
        self._monkeypatch.setattr(InMemoryPageStageRepository, 'save_many', recording_save_many)
