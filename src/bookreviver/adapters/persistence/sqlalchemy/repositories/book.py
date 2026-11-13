"""The repositories of a book, bound together to the session of one transaction of the file of the book."""

from typing import TYPE_CHECKING

from bookreviver.adapters.persistence.sqlalchemy.book_place import SqlAlchemyBookPlaceRepository
from bookreviver.adapters.persistence.sqlalchemy.book_record import SqlAlchemyBookRecordRepository
from bookreviver.adapters.persistence.sqlalchemy.groups import PageGroups
from bookreviver.adapters.persistence.sqlalchemy.repositories.pages import (
    SqlAlchemyPageRepository,
    SqlAlchemyPaginationSectionRepository,
)
from bookreviver.adapters.persistence.sqlalchemy.repositories.recipes import (
    SqlAlchemyRecipeProfileRepository,
    SqlAlchemyRecipeRepository,
)
from bookreviver.adapters.persistence.sqlalchemy.repositories.sources import (
    SqlAlchemyScanRepository,
    SqlAlchemySourceRepository,
)
from bookreviver.adapters.persistence.sqlalchemy.repositories.stages import SqlAlchemyPageStageRepository
from bookreviver.adapters.persistence.sqlalchemy.repositories.step_state import (
    SqlAlchemyPageHistoryRepository,
    SqlAlchemyPageStepStateRepository,
    SqlAlchemyStepValuesRepository,
)
from bookreviver.adapters.persistence.sqlalchemy.repositories.versions import SqlAlchemyPageVersionRepository
from bookreviver.adapters.persistence.sqlalchemy.step_types import StepTypes
from bookreviver.ports.persistence import BookRepositories

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from bookreviver.ports.ordering import OrderKeys


class SqlAlchemyBookRepositories(BookRepositories):
    """Every repository of a book, bound to the session of one transaction of the file of the book.

    The database of the book builds one for each transaction and hands it to the work, so the repositories share the
    session, and with it the transaction, and are dropped with it.

    :ivar book_record: Repository of the record of the book, bound to the session.
    :ivar sources: Source repository bound to the session.
    :ivar scans: Scan repository bound to the session.
    :ivar pages: Page repository bound to the session.
    :ivar pagination_sections: Pagination section repository bound to the session.
    :ivar groups: The groups of the pages of the book on the session, which the work prunes after a change.
    :ivar page_versions: Page version repository bound to the session.
    :ivar page_stages: Page stage repository bound to the session.
    :ivar page_step_states: Page step state repository bound to the session.
    :ivar page_history: Page history repository bound to the session.
    :ivar step_values: Step values repository bound to the session.
    :ivar recipes: Recipe repository bound to the session.
    :ivar recipe_profiles: Recipe profile repository bound to the session.
    :ivar book_places: Book place repository bound to the session.
    """

    def __init__(self, session: Session, *, order_keys: OrderKeys) -> None:
        """Bind every repository of a book to ``session``.

        :param session: Session of the transaction, owned and closed by the database that runs the work.
        :type session: Session
        :param order_keys: The order keys that place the steps of a recipe.
        :type order_keys: OrderKeys
        """
        self.groups = PageGroups(session)
        self.book_record = SqlAlchemyBookRecordRepository(session)
        self.sources = SqlAlchemySourceRepository(session)
        self.scans = SqlAlchemyScanRepository(session)
        self.pages = SqlAlchemyPageRepository(session, self.groups)
        self.pagination_sections = SqlAlchemyPaginationSectionRepository(session)
        self.page_versions = SqlAlchemyPageVersionRepository(session)
        self.page_stages = SqlAlchemyPageStageRepository(session)
        types = StepTypes(session)
        self.page_step_states = SqlAlchemyPageStepStateRepository(session, types)
        self.page_history = SqlAlchemyPageHistoryRepository(session, self.groups, types)
        self.step_values = SqlAlchemyStepValuesRepository(session, self.groups, types)
        self.recipes = SqlAlchemyRecipeRepository(session, order_keys)
        self.recipe_profiles = SqlAlchemyRecipeProfileRepository(session)
        self.book_places = SqlAlchemyBookPlaceRepository(session)
