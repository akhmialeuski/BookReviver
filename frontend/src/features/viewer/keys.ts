/**
 * The keys of the viewer and what they do: the arrow keys turn pages, Home and End go to the ends of the book.
 *
 * Kept apart from the screen so the mapping can be tested, and so the rule for when a key belongs to the viewer is
 * written once: a key pressed inside a form field, with a modifier, or while a dialog is open is not a page turn.
 */

/** What a key asks the viewer to do. */
export const ViewerKeyAction = {
  Previous: 'previous',
  Next: 'next',
  First: 'first',
  Last: 'last',
} as const;

/** One action of the viewer keys (derived from {@link ViewerKeyAction}). */
export type ViewerKeyAction = (typeof ViewerKeyAction)[keyof typeof ViewerKeyAction];

const ACTION_OF_KEY: Readonly<Record<string, ViewerKeyAction>> = {
  ArrowLeft: ViewerKeyAction.Previous,
  ArrowRight: ViewerKeyAction.Next,
  Home: ViewerKeyAction.First,
  End: ViewerKeyAction.Last,
};

const EDITABLE_TAGS: ReadonlySet<string> = new Set(['INPUT', 'TEXTAREA', 'SELECT']);

/** The part of a keyboard event the mapping reads, so a test needs no real event. */
export interface KeyPress {
  key: string;
  altKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
  target: EventTarget | null;
}

/** Tell whether a key pressed on this element is typing or a control's own key, which the viewer leaves alone. */
export function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  return EDITABLE_TAGS.has(target.tagName) || target.isContentEditable;
}

/**
 * Decide what a key press asks of the viewer.
 *
 * @param press The key press.
 * @param dialogOpen Whether a dialog is open, in which case the keys belong to it.
 * @returns The action, or null when the key is not the viewer's.
 */
export function viewerKeyAction(press: KeyPress, dialogOpen: boolean): ViewerKeyAction | null {
  if (dialogOpen || press.altKey || press.ctrlKey || press.metaKey || press.shiftKey) {
    return null;
  }
  if (isEditableTarget(press.target)) {
    return null;
  }
  return ACTION_OF_KEY[press.key] ?? null;
}
