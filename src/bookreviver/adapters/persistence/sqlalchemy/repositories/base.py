"""The generic port repositories of the adapter, each over the row repository of one table and a mapper."""

from typing import TYPE_CHECKING, override

from bookreviver.domain.errors import NotFoundError
from bookreviver.ports.persistence import (
    KeyedRepository,
    Repository,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from advanced_alchemy.base import ModelProtocol

    from bookreviver.adapters.persistence.sqlalchemy.mappers import RowMapper
    from bookreviver.adapters.persistence.sqlalchemy.rows import RowRepository


class SqlAlchemyRepository[EntityT, IdT, RowT: ModelProtocol](Repository[EntityT, IdT]):
    """The generic operations of a port repository, delegated to a row repository and mapped to entities."""

    def __init__(self, *, rows: RowRepository[RowT], mapper: RowMapper[EntityT, RowT]) -> None:
        """Create the repository over one table.

        :param rows: Row repository of the entity's table, bound to the session of the transaction.
        :type rows: RowRepository[RowT]
        :param mapper: Translation between the entity and its row.
        :type mapper: RowMapper[EntityT, RowT]
        """
        self._rows = rows
        self._mapper = mapper

    @override
    def get(self, entity_id: IdT) -> EntityT:
        """Return the entity with this identifier.

        :param entity_id: Identifier of the entity.
        :type entity_id: IdT
        :returns: The stored entity.
        :rtype: EntityT
        :raises NotFoundError: If no entity has this identifier.
        """
        return self._mapper.to_entity(self._rows.get(entity_id))

    @override
    def add(self, entity: EntityT) -> EntityT:
        """Store a new entity.

        :param entity: Entity to store, with its identifier already assigned.
        :type entity: EntityT
        :returns: The entity as stored.
        :rtype: EntityT
        """
        return self._mapper.to_entity(self._rows.add(self._mapper.to_row(entity)))

    @override
    def add_many(self, entities: Sequence[EntityT]) -> Sequence[EntityT]:
        """Store several new entities in one statement, which the database applies whole or not at all.

        :param entities: Entities to store, with their identifiers already assigned.
        :type entities: Sequence[EntityT]
        :returns: The entities as stored, in the given order.
        :rtype: Sequence[EntityT]
        """
        # An insert of no rows is no statement at all
        if not entities:
            return entities
        rows = self._rows.add_many([self._mapper.to_row(entity) for entity in entities])
        return [self._mapper.to_entity(row) for row in rows]

    @override
    def update(self, entity: EntityT) -> EntityT:
        """Replace the stored state of an existing entity.

        :param entity: Entity with its new state.
        :type entity: EntityT
        :returns: The entity as stored.
        :rtype: EntityT
        :raises NotFoundError: If the entity is not stored.
        """
        return self._mapper.to_entity(self._rows.update(self._mapper.to_row(entity)))

    @override
    def delete(self, entity_id: IdT) -> None:
        """Remove the entity, and through the cascading foreign keys everything that belongs to it.

        :param entity_id: Identifier of the entity.
        :type entity_id: IdT
        :raises NotFoundError: If no entity has this identifier.
        """
        self._rows.delete(entity_id)


class SqlAlchemyKeyedRepository[EntityT, KeyT, RowT: ModelProtocol](
    SqlAlchemyRepository[EntityT, KeyT, RowT], KeyedRepository[EntityT, KeyT]
):
    """The operations of a table whose entities are stored under a key, built on one upsert of the row repository."""

    @override
    def save_many(self, entities: Sequence[EntityT]) -> Sequence[EntityT]:
        """Store several entities in one upsert, each replacing the stored one of its key, all of them or none.

        :param entities: Entities to store.
        :type entities: Sequence[EntityT]
        :returns: The entities as stored, in the given order.
        :rtype: Sequence[EntityT]
        :raises NotFoundError: If an entity one of them refers to is not stored.
        """
        rows = self._rows.replace_many([self._mapper.to_row(entity) for entity in entities])
        return [self._mapper.to_entity(row) for row in rows]

    @override
    def find(self, key: KeyT) -> EntityT | None:
        """Return the entity stored under the key, read as :meth:`get` reads it.

        :param key: Key of the entity.
        :type key: KeyT
        :returns: The entity, or None.
        :rtype: EntityT | None
        """
        try:
            return self.get(key)
        except NotFoundError:
            return None
