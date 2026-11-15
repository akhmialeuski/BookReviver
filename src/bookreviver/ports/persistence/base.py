"""The generic repository ports, which every repository of an aggregate derives from."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


class Repository[EntityT, IdT](ABC):
    """Storage of one kind of entity addressed by its identifier."""

    @abstractmethod
    def get(self, entity_id: IdT) -> EntityT:
        """Return the entity.

        :param entity_id: Identifier of the entity.
        :type entity_id: IdT
        :returns: The stored entity.
        :rtype: EntityT
        :raises NotFoundError: If no entity has this identifier.
        """

    @abstractmethod
    def add(self, entity: EntityT) -> EntityT:
        """Store a new entity and return it as stored.

        :param entity: Entity to store, with its identifier already assigned.
        :type entity: EntityT
        :returns: The entity as stored.
        :rtype: EntityT
        :raises ConflictError: If an entity with this identifier, or with a value its port declares unique, is
                               already stored.
        :raises NotFoundError: If an entity it refers to, such as its book, is not stored.
        """

    @abstractmethod
    def add_many(self, entities: Sequence[EntityT]) -> Sequence[EntityT]:
        """Store several new entities at once, all of them or, on an error, none.

        After an error the change that holds the write rolls back when its work raises the exception, as a database
        requires.

        :param entities: Entities to store, with their identifiers already assigned.
        :type entities: Sequence[EntityT]
        :returns: The entities as stored, in the given order.
        :rtype: Sequence[EntityT]
        :raises ConflictError: If an identifier or a unique value of one entity is stored already or given twice.
        :raises NotFoundError: If an entity one of them refers to is not stored.
        """

    @abstractmethod
    def update(self, entity: EntityT) -> EntityT:
        """Replace the stored state of an existing entity and return it as stored.

        :param entity: Entity with its new state.
        :type entity: EntityT
        :returns: The entity as stored.
        :rtype: EntityT
        :raises NotFoundError: If the entity, or an entity it refers to, is not stored.
        :raises ConflictError: If its new state takes a value its port declares unique from another entity.
        """

    @abstractmethod
    def delete(self, entity_id: IdT) -> None:
        """Remove the entity, together with everything that belongs to it.

        :param entity_id: Identifier of the entity.
        :type entity_id: IdT
        :raises NotFoundError: If no entity has this identifier.
        """


class KeyedRepository[EntityT, KeyT](Repository[EntityT, KeyT]):
    """Storage of entities addressed by a key of several values, written by replacing the entity stored under the key.

    Such an entity has no identifier of its own, so ``save`` and ``save_many`` store it whether or not the key is taken,
    where ``add`` refuses a taken key and ``update`` a free one.
    """

    def save(self, entity: EntityT) -> EntityT:
        """Store the entity, replacing the one stored under the same key.

        :param entity: Entity to store.
        :type entity: EntityT
        :returns: The entity as stored.
        :rtype: EntityT
        :raises NotFoundError: If an entity it refers to, such as its page, is not stored.
        """
        [stored] = self.save_many([entity])
        return stored

    @abstractmethod
    def save_many(self, entities: Sequence[EntityT]) -> Sequence[EntityT]:
        """Store several entities at once, each replacing the one under its key, all of them or, on an error, none.

        After an error the change that holds the write rolls back when its work raises the exception, as a database
        requires.

        :param entities: Entities to store.
        :type entities: Sequence[EntityT]
        :returns: The entities as stored, in the given order.
        :rtype: Sequence[EntityT]
        :raises NotFoundError: If an entity one of them refers to, such as its page, is not stored.
        """

    @abstractmethod
    def find(self, key: KeyT) -> EntityT | None:
        """Return the entity stored under the key.

        :param key: Key of the entity.
        :type key: KeyT
        :returns: The entity, or None when nothing is stored under the key.
        :rtype: EntityT | None
        """
