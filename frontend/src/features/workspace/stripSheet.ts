import { createContext, useContext } from 'react';

/**
 * What the strip of pages can tell the sheet it stands in.
 *
 * In the narrow layout the strip opens as a sheet over the canvas, and picking a page closes it, so the page is in sight
 * at once. The strip reaches the sheet through this context and calls the function when a page is picked. In the wide
 * layout there is no sheet, and the function does nothing.
 */

const StripSheetContext = createContext<() => void>(() => undefined);

export const StripSheetProvider = StripSheetContext.Provider;

/** The function to call once a page is picked in the strip. */
export function useAfterPick(): () => void {
  return useContext(StripSheetContext);
}
