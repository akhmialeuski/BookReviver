/**
 * Keeping the page in the address of the book list inside the pages the list has.
 *
 * The page number is in the address so it can be linked, which also means it can name a page that no longer exists:
 * deleting the only book on the last page leaves the address on a page the server answers with no items. The list
 * compares the page with the page count it was just given and moves to the last page that exists.
 */

/** Return the page to show for a requested page, given how many pages the server reports for the collection. */
export function pageToShow(requested: number, pages: number): number {
  return Math.max(1, Math.min(requested, pages));
}
