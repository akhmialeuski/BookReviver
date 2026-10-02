/**
 * The state of the viewer that lives in the address: which page is open and whether two pages are shown.
 *
 * The router hands `validateSearch` whatever it parsed from the query string, so every value is checked here and a
 * value of the wrong shape is dropped rather than carried into the screen. The page is a `PageId`, never a position,
 * so a link keeps pointing at the same page after pages are moved.
 */

/** What the viewer keeps in the search params of its route. */
export interface ViewerSearch {
  /** Identifier of the page that is open, or absent for the first page of the book. */
  page?: string;
  /** Present and true when two pages are shown as a spread, absent for one page. */
  spread?: true;
}

const SPREAD_ON_VALUES: ReadonlySet<unknown> = new Set([true, 1, 'true', '1']);

/**
 * Read an identifier from a search param.
 *
 * The router decodes an identifier that is all digits into a number, so a number is accepted and written back as
 * text. An empty text, an object or anything else is no identifier.
 *
 * @param value The decoded value of the param.
 * @returns The trimmed identifier, or undefined.
 */
export function parseIdentifier(value: unknown): string | undefined {
  const text = typeof value === 'number' ? String(value) : value;
  return typeof text === 'string' && text.trim() !== '' ? text.trim() : undefined;
}

/**
 * Read the search params of the viewer route.
 *
 * The router decodes `?spread=true` into a boolean and `?spread=1` into a number, so each of those shapes is
 * accepted for the spread, and the page is read by {@link parseIdentifier}. Nothing else is: an empty page, an object
 * or an unknown spread value are the same as leaving the parameter out.
 */
export function parseViewerSearch(search: Record<string, unknown>): ViewerSearch {
  const { page, spread } = search;
  const result: ViewerSearch = {};
  const identifier = parseIdentifier(page);
  if (identifier !== undefined) {
    result.page = identifier;
  }
  if (SPREAD_ON_VALUES.has(spread)) {
    result.spread = true;
  }
  return result;
}
