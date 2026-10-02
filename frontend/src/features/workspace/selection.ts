import type { PageSchema } from '@/api';
import { selectRange } from '@/features/pages/selection';

/**
 * What a click in the grid does to the selected pages. The range between two pages is `selectRange` of the page strip
 * of the book, which the grid reuses.
 */

/** The modifier keys held during a click. */
export interface ClickModifiers {
  /** Shift: extend from the page last clicked to this one. */
  range: boolean;
  /** Ctrl or Cmd: add this page to the selection or take it out. */
  toggle: boolean;
}

/** The selection after a click, and the page the next range starts from. */
export interface SelectionState {
  selected: ReadonlySet<string>;
  anchorId: string | null;
}

/**
 * Work out the selection after a click on a page.
 *
 * A plain click selects that page alone. Ctrl or Cmd adds the page, or takes it out when it was selected. Shift selects
 * the pages from the anchor to the one clicked, and with Ctrl or Cmd as well adds them to what was selected. The
 * anchor moves to the page clicked, except on a click with Shift, which keeps it so the range can be stretched again.
 *
 * @param pages The pages the grid lists, in order.
 * @param state The selection before the click.
 * @param clickedId The page clicked.
 * @param modifiers The keys held.
 */
export function selectionAfterClick(
  pages: readonly PageSchema[],
  state: SelectionState,
  clickedId: string,
  modifiers: ClickModifiers,
): SelectionState {
  if (modifiers.range) {
    const range = selectRange(pages, state.anchorId, clickedId);
    const selected = modifiers.toggle ? new Set([...state.selected, ...range]) : new Set(range);
    return { selected, anchorId: state.anchorId ?? clickedId };
  }
  if (modifiers.toggle) {
    const selected = new Set(state.selected);
    if (!selected.delete(clickedId)) {
      selected.add(clickedId);
    }
    return { selected, anchorId: clickedId };
  }
  return { selected: new Set([clickedId]), anchorId: clickedId };
}
