"""In-memory persistence, the reference adapter the service tests run against."""

from bookreviver.adapters.persistence.memory.unit_of_work import InMemoryDatabase, InMemoryUnitOfWork

__all__ = ['InMemoryDatabase', 'InMemoryUnitOfWork']
