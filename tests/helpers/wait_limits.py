"""Wait limits of the units of work that the tests of the SQL write blocks share with their fixtures."""

# Short enough that a block refused the lock gives up at once
IMPATIENT_SECONDS: float = 0.05
