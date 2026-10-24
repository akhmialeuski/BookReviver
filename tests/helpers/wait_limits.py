"""Wait limits of the catalogue databases that the tests of the SQL write blocks share with their fixtures."""

# Short enough that a block refused the lock gives up at once
IMPATIENT_SECONDS: float = 0.05
