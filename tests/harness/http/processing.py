"""A book of one scanned page that the processing API is tried on, and the requests the tests of that API share."""

import io
from typing import TYPE_CHECKING, NamedTuple

from PIL import Image

from bookreviver.api.schemas.processing import RecipeSchema
from bookreviver.domain.images import Rendition
from bookreviver.domain.keys import BookKeys
from tests.harness.builders.seeding import BookLayout, ScanLayout, SourceLayout, seed_book
from tests.harness.http.pages import BOOKS_PATH

if TYPE_CHECKING:
    import httpx

    from bookreviver.domain.accounts import Actor
    from bookreviver.domain.ids import BookId
    from bookreviver.domain.pages import Page, RecipeKind
    from bookreviver.domain.sources import Scan
    from bookreviver.ports.storage import AssetStore
    from tests.harness.adapters.database import BookDatabasesFactory, CatalogueDatabaseFactory
    from tests.harness.adapters.storage import SharedBookDirectories

# The member of a page of a collection that holds its items
ITEMS: str = 'items'
SCAN_SIZE_PX: tuple[int, int] = (120, 160)


class Book(NamedTuple):
    """A book of the signed-in account with one page cut from a scan whose image is stored.

    :ivar book_id: Identifier of the book.
    :ivar page: The page the import made of the scan.
    """

    book_id: BookId
    page: Page

    @property
    def path(self) -> str:
        """The path of the book."""
        return f'{BOOKS_PATH}/{self.book_id}'

    @property
    def page_path(self) -> str:
        """The path of the page."""
        return f'{self.path}/pages/{self.page.id}'


async def store_scan_images(asset_store: AssetStore, scan: Scan) -> None:
    """Store the full image and the preview of a scan as real JPEGs.

    :param asset_store: Asset store of the book owning the scan, the application's own.
    :type asset_store: AssetStore
    :param scan: The scan, whose renditions are ready.
    :type scan: Scan
    """
    buffer = io.BytesIO()
    Image.new('L', SCAN_SIZE_PX, color=200).save(buffer, format='JPEG')
    for rendition in (Rendition.FULL_JPEG, Rendition.PREVIEW):
        async with asset_store.writable(BookKeys.scan_rendition(scan, rendition)) as path:
            path.write_bytes(buffer.getvalue())


async def scanned_book(
    *,
    catalogue: CatalogueDatabaseFactory,
    directories: SharedBookDirectories,
    actor: Actor,
    databases: BookDatabasesFactory,
) -> Book:
    """Commit a book of one page and store the full image of its scan as a real JPEG.

    :param catalogue: Function opening a new catalogue database over the database of the test.
    :type catalogue: CatalogueDatabaseFactory
    :param directories: Opens the source store and the asset store of a book of the test.
    :type directories: SharedBookDirectories
    :param actor: The signed-in account, which owns the book.
    :type actor: Actor
    :param databases: Function opening a new opener of the databases of the books of the test.
    :type databases: BookDatabasesFactory
    :returns: The stored book.
    :rtype: Book
    """
    layout = BookLayout.scanned(SourceLayout.of(1, scan=ScanLayout(ready=True)))
    book = await seed_book(catalogue(), layout, owner=actor.account_id, databases=databases())
    await store_scan_images(directories.assets(book.book_id), book.scans[0])
    return Book(book_id=book.book_id, page=book.pages[0])


async def set_strength(client: httpx.AsyncClient, book: Book, step_id: str, value: int) -> httpx.Response:
    """Set the strength of a step for the page of the book.

    :param client: Client of the running application.
    :type client: httpx.AsyncClient
    :param book: Book of the signed-in account.
    :type book: Book
    :param step_id: Identifier of the step.
    :type step_id: str
    :param value: The strength the page uses.
    :type value: int
    :returns: The answer.
    :rtype: httpx.Response
    """
    body = {'scope': 'pages', 'page_ids': [str(book.page.id)], 'value': value}
    return await client.put(f'{book.path}/stages/geometry/steps/{step_id}/values/strength', json=body)


async def recipes_of(client: httpx.AsyncClient, book: Book, stage: str) -> dict[RecipeKind, RecipeSchema]:
    """List the recipes of a stage through the API, by the kind of page.

    :param client: Client of the running application.
    :type client: httpx.AsyncClient
    :param book: Book of the signed-in account.
    :type book: Book
    :param stage: The stage.
    :type stage: str
    :returns: The recipe of each kind.
    :rtype: dict[RecipeKind, RecipeSchema]
    """
    listed = await client.get(f'{book.path}/stages/{stage}/recipes')
    return {recipe.kind: recipe for recipe in (RecipeSchema.model_validate(item) for item in listed.json()[ITEMS])}
