"""Unit of work over one SQLAlchemy session, which is one database transaction.

The session is opened per HTTP request or background job by the application's ``DatabaseProvider`` and closed by it,
so the unit of work owns no connection. It binds every port repository to that session, which makes every
change they make part of one transaction, visible to others only after :meth:`SqlAlchemyUnitOfWork.commit`.
"""

from typing import TYPE_CHECKING, override

from bookreviver.adapters.persistence.sqlalchemy.repositories import (
    SqlAlchemyBookPlaceRepository,
    SqlAlchemyJobRepository,
    SqlAlchemyPageRepository,
    SqlAlchemyPageStageRepository,
    SqlAlchemyPageStepChangeRepository,
    SqlAlchemyPageStepStateRepository,
    SqlAlchemyPageVersionRepository,
    SqlAlchemyPaginationSectionRepository,
    SqlAlchemyProjectRepository,
    SqlAlchemyRecipeProfileRepository,
    SqlAlchemyRecipeRepository,
    SqlAlchemyRecipeRuleRepository,
    SqlAlchemyScanRepository,
    SqlAlchemySourceRepository,
)
from bookreviver.ports.persistence import UnitOfWork

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class SqlAlchemyUnitOfWork(UnitOfWork):
    """Repositories sharing the session of one request or job, committed or rolled back through the session.

    :ivar projects: Project repository bound to the session.
    :ivar sources: Source repository bound to the session.
    :ivar scans: Scan repository bound to the session.
    :ivar pages: Page repository bound to the session.
    :ivar pagination_sections: Pagination section repository bound to the session.
    :ivar page_versions: Page version repository bound to the session.
    :ivar page_stages: Page stage repository bound to the session.
    :ivar page_step_states: Page step state repository bound to the session.
    :ivar page_step_changes: Page step change repository bound to the session.
    :ivar recipes: Recipe repository bound to the session.
    :ivar recipe_rules: Recipe rule repository bound to the session.
    :ivar recipe_profiles: Recipe profile repository bound to the session.
    :ivar jobs: Job repository bound to the session.
    :ivar book_places: Book place repository bound to the session.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repositories to ``session``.

        :param session: Session of the current request or job, owned and closed by its provider.
        :type session: AsyncSession
        """
        self._session = session
        self.projects = SqlAlchemyProjectRepository(session)
        self.sources = SqlAlchemySourceRepository(session)
        self.scans = SqlAlchemyScanRepository(session)
        self.pages = SqlAlchemyPageRepository(session)
        self.pagination_sections = SqlAlchemyPaginationSectionRepository(session)
        self.page_versions = SqlAlchemyPageVersionRepository(session)
        self.page_stages = SqlAlchemyPageStageRepository(session)
        self.page_step_states = SqlAlchemyPageStepStateRepository(session)
        self.page_step_changes = SqlAlchemyPageStepChangeRepository(session)
        self.recipes = SqlAlchemyRecipeRepository(session)
        self.recipe_rules = SqlAlchemyRecipeRuleRepository(session)
        self.recipe_profiles = SqlAlchemyRecipeProfileRepository(session)
        self.jobs = SqlAlchemyJobRepository(session)
        self.book_places = SqlAlchemyBookPlaceRepository(session)

    @override
    async def commit(self) -> None:
        """Commit the transaction, making every change since the last commit durable and visible."""
        await self._session.commit()

    @override
    async def rollback(self) -> None:
        """Roll the transaction back, discarding every change since the last commit."""
        await self._session.rollback()
