"""Reading the pages of a book through the API of the running application."""

from typing import TYPE_CHECKING

from fastapi_pagination import Page

from bookreviver.api.schemas.pages import PageSchema

if TYPE_CHECKING:
    import httpx

    from bookreviver.domain.ids import BookId, PageId

BOOKS_PATH: str = '/api/v1/books'


async def read_page(client: httpx.AsyncClient, book_id: BookId, page_id: PageId) -> PageSchema:
    """Read one page of a book.

    :param client: Client of the running application.
    :type client: httpx.AsyncClient
    :param book_id: The book.
    :type book_id: BookId
    :param page_id: Identifier of the page.
    :type page_id: PageId
    :returns: The page resource.
    :rtype: PageSchema
    """
    return PageSchema.model_validate_json((await client.get(f'{BOOKS_PATH}/{book_id}/pages/{page_id}')).content)


async def read_labels(client: httpx.AsyncClient, book_id: BookId) -> list[str]:
    """Read the labels of the pages of a book in book order.

    :param client: Client of the running application.
    :type client: httpx.AsyncClient
    :param book_id: The book.
    :type book_id: BookId
    :returns: The label of each page in the manifest.
    :rtype: list[str]
    """
    response = await client.get(f'{BOOKS_PATH}/{book_id}/pages')
    return [item.label for item in Page[PageSchema].model_validate_json(response.content).items]
