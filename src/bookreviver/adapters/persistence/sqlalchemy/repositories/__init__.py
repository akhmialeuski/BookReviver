"""Port repositories of the SQLAlchemy adapter, each over the row repository of its table.

Each port repository works on two levels. A row repository of
:mod:`bookreviver.adapters.persistence.sqlalchemy.rows` supplies every generic query of one table, and a mapper from
:mod:`bookreviver.adapters.persistence.sqlalchemy.mappers` turns its rows into domain entities. The port repository
adds only the queries specific to BookReviver, such as the counts of a book. The repositories are
synchronous, because the catalogue database runs the whole work of a use case in one worker thread.
:class:`SqlAlchemyCatalogueRepositories` binds the repositories of the catalogue to the session of a transaction of the
catalogue, and :class:`SqlAlchemyBookRepositories` those of a book to a session of the file of that book. The row
repositories report the database's checks as the domain errors the port promises.
"""

from bookreviver.adapters.persistence.sqlalchemy.repositories.base import (
    SqlAlchemyKeyedRepository,
    SqlAlchemyRepository,
)
from bookreviver.adapters.persistence.sqlalchemy.repositories.book import SqlAlchemyBookRepositories
from bookreviver.adapters.persistence.sqlalchemy.repositories.library import (
    SqlAlchemyCatalogueRepositories,
    SqlAlchemyLibraryRepository,
)
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

__all__ = [
    'SqlAlchemyBookRepositories',
    'SqlAlchemyCatalogueRepositories',
    'SqlAlchemyKeyedRepository',
    'SqlAlchemyLibraryRepository',
    'SqlAlchemyPageHistoryRepository',
    'SqlAlchemyPageRepository',
    'SqlAlchemyPageStageRepository',
    'SqlAlchemyPageStepStateRepository',
    'SqlAlchemyPageVersionRepository',
    'SqlAlchemyPaginationSectionRepository',
    'SqlAlchemyRecipeProfileRepository',
    'SqlAlchemyRecipeRepository',
    'SqlAlchemyRepository',
    'SqlAlchemyScanRepository',
    'SqlAlchemySourceRepository',
    'SqlAlchemyStepValuesRepository',
]
