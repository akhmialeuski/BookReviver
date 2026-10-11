"""A gate that holds a ``change_book`` block open, so a test can race another block of the same book against it."""

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import anyio
from attrs import define, field

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import pytest

    from bookreviver.domain.entities import Project
    from bookreviver.domain.ids import ProjectId
    from bookreviver.ports.persistence import UnitOfWork


@define
class BookGate:
    """The two events of one gated unit of work, which order a test with no sleep.

    :ivar entered: Set when the block of the unit of work holds the lock of its book.
    :ivar proceed: Set by the test to let the block carry on.
    """

    entered: anyio.Event = field(factory=anyio.Event)
    proceed: anyio.Event = field(factory=anyio.Event)


def hold_book(uow: UnitOfWork, monkeypatch: pytest.MonkeyPatch) -> BookGate:
    """Make the ``change_book`` blocks of a unit of work stop right after they are entered, until the test lets go.

    The block holds the lock of the book while it waits, so a second block of the same book started meanwhile has to
    wait for it. The test learns that the first block is inside through ``entered`` and lets it carry on with
    ``proceed``, with no sleep between the two. It works on any adapter, since it wraps the method of the instance.

    :param uow: Unit of work whose blocks are held.
    :type uow: UnitOfWork
    :param monkeypatch: Fixture that restores the method after the test.
    :type monkeypatch: pytest.MonkeyPatch
    :returns: The gate of the unit of work.
    :rtype: BookGate
    """
    gate = BookGate()
    real = uow.change_book

    @asynccontextmanager
    async def held(project_id: ProjectId) -> AsyncIterator[Project]:
        """Enter the block, tell the test, and wait for it before the body of the block runs.

        :param project_id: Project whose book is changed.
        :type project_id: ProjectId
        :returns: Iterator yielding the project once the test has let the block go.
        :rtype: AsyncIterator[Project]
        """
        async with real(project_id) as project:
            gate.entered.set()
            await gate.proceed.wait()
            yield project

    monkeypatch.setattr(uow, 'change_book', held)
    return gate
