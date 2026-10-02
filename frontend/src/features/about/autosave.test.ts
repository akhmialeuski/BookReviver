import { describe, expect, it } from 'vitest';
import { formatSavedAt, type SaveFacts, saveState } from './autosave';

/**
 * What the line under the form says, and how it writes the moment of a save.
 */

const QUIET: SaveFacts = {
  dirty: false,
  failed: false,
  saving: false,
  invalid: false,
  saved: false,
};

describe('saveState', () => {
  it('says nothing was changed before the first change', () => {
    expect(saveState(QUIET)).toBe('idle');
  });

  it('says saving while changes wait or a request is in flight', () => {
    expect(saveState({ ...QUIET, dirty: true })).toBe('saving');
    expect(saveState({ ...QUIET, saving: true, saved: true })).toBe('saving');
  });

  it('says saved once the changes are taken', () => {
    expect(saveState({ ...QUIET, saved: true })).toBe('saved');
  });

  it('puts a refusal before everything else, because the person must act on it', () => {
    expect(saveState({ ...QUIET, failed: true, dirty: true, invalid: true, saved: true })).toBe(
      'failed',
    );
  });

  it('names a field that is not sent only when nothing else is on its way', () => {
    expect(saveState({ ...QUIET, invalid: true, saved: true })).toBe('invalid');
    expect(saveState({ ...QUIET, invalid: true, dirty: true })).toBe('saving');
  });
});

describe('formatSavedAt', () => {
  it('writes the day and the time of day on a 24-hour clock', () => {
    expect(formatSavedAt(new Date(2026, 9, 2, 11, 2))).toBe('Oct 2, 11:02');
    expect(formatSavedAt(new Date(2026, 9, 2, 17, 45))).toBe('Oct 2, 17:45');
  });
});
