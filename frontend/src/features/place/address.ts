import type {
  BookPlaceBody,
  BookPlaceSchema,
  CanvasPositionSchema,
  CompareMode,
  PageFilter,
  PlaceMode,
  Stage,
  ViewMode,
} from '@/api';

/**
 * Where a reader is in a book, as the address of the screen says it, and the conversions to and from the place the
 * server keeps.
 *
 * The address of a screen leaves out what has its default, so a link to the plain stage stays short, while the place
 * names every field. These functions fill the defaults in and take them out, so an address and a place can be compared.
 */

const DEFAULT_VIEW: ViewMode = 'page';
const DEFAULT_COMPARE: CompareMode = 'off';
const DEFAULT_FILTER: PageFilter = 'all';

/** What the address of a screen says about the place; a field left out has its default. */
export interface PlaceAddress {
  mode: PlaceMode;
  stage: Stage;
  page?: string;
  scan?: string;
  source?: string;
  view?: ViewMode;
  compare?: CompareMode;
  filter?: PageFilter;
}

/** The address with every field given, so two can be compared. */
interface FullAddress {
  mode: PlaceMode;
  stage: Stage;
  page: string | null;
  scan: string | null;
  source: string | null;
  view: ViewMode;
  compare: CompareMode;
  filter: PageFilter;
}

function fullAddress(address: PlaceAddress): FullAddress {
  return {
    mode: address.mode,
    stage: address.stage,
    page: address.page ?? null,
    scan: address.scan ?? null,
    source: address.source ?? null,
    view: address.view ?? DEFAULT_VIEW,
    compare: address.compare ?? DEFAULT_COMPARE,
    filter: address.filter ?? DEFAULT_FILTER,
  };
}

/** Tell whether two addresses name the same place, whichever of them leaves a default out. */
export function sameAddress(first: PlaceAddress, second: PlaceAddress): boolean {
  const a = fullAddress(first);
  const b = fullAddress(second);
  return (Object.keys(a) as (keyof FullAddress)[]).every((field) => a[field] === b[field]);
}

/** Read the address out of a place. */
export function addressOfPlace(place: BookPlaceSchema): PlaceAddress {
  return {
    mode: place.mode,
    stage: place.stage,
    ...(place.page_id === null ? {} : { page: place.page_id }),
    ...(place.scan_id === null ? {} : { scan: place.scan_id }),
    ...(place.source_id === null ? {} : { source: place.source_id }),
    view: place.view,
    compare: place.compare,
    filter: place.filter,
  };
}

/**
 * Build the body that writes a place.
 *
 * @param address Where the reader is.
 * @param canvas The zoom and centre of the canvas, or null for the fitted view.
 * @param stripPageId The first page in sight in the strip or the grid, or null for the top.
 */
export function bodyOf(
  address: PlaceAddress,
  canvas: CanvasPositionSchema | null,
  stripPageId: string | null,
): BookPlaceBody {
  const full = fullAddress(address);
  return {
    mode: full.mode,
    stage: full.stage,
    page_id: full.page,
    scan_id: full.scan,
    source_id: full.source,
    view: full.view,
    compare: full.compare,
    filter: full.filter,
    canvas,
    strip_page_id: stripPageId,
  };
}

/** Build the place a body will become, for the screens that read it before the server has answered. */
export function placeOfBody(body: BookPlaceBody, writtenAt: Date): BookPlaceSchema {
  return {
    mode: body.mode,
    stage: body.stage,
    page_id: body.page_id ?? null,
    scan_id: body.scan_id ?? null,
    source_id: body.source_id ?? null,
    view: body.view ?? DEFAULT_VIEW,
    compare: body.compare ?? DEFAULT_COMPARE,
    filter: body.filter ?? DEFAULT_FILTER,
    canvas: body.canvas ?? null,
    strip_page_id: body.strip_page_id ?? null,
    updated_at: writtenAt.toISOString(),
  };
}
