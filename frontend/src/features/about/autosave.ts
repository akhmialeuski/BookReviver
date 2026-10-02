/**
 * Where the autosave of the About tab stands, as the line under the form words it.
 */

const LOCALE = 'en';

const timeFormat = new Intl.DateTimeFormat(LOCALE, {
  day: 'numeric',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
});

/** What the line under the form says. */
export type SaveState = 'idle' | 'saving' | 'saved' | 'failed' | 'invalid';

/** The facts the state is made from. */
export interface SaveFacts {
  /** Changes wait to be sent or are on their way. */
  readonly dirty: boolean;
  /** The server refused the changes that were sent last. */
  readonly failed: boolean;
  /** A request is in flight. */
  readonly saving: boolean;
  /** Some field holds a value that is not sent. */
  readonly invalid: boolean;
  /** Something has been saved since the tab opened. */
  readonly saved: boolean;
}

/**
 * Decide what the line under the form says.
 *
 * A refusal outranks everything, because the person must act on it. A field that is not sent is told about once
 * nothing else is on its way, so the line does not flicker between "saving" and the reason while someone types.
 *
 * @param facts What is known about the changes.
 * @returns The state to word.
 */
export function saveState(facts: SaveFacts): SaveState {
  if (facts.failed) {
    return 'failed';
  }
  if (facts.saving || facts.dirty) {
    return 'saving';
  }
  if (facts.invalid) {
    return 'invalid';
  }
  return facts.saved ? 'saved' : 'idle';
}

/**
 * Write the moment of a save the way the line under the form shows it, such as `Oct 2, 11:02`.
 *
 * @param moment When the save finished.
 * @returns The date and the time of day.
 */
export function formatSavedAt(moment: Date): string {
  return timeFormat.format(moment);
}
