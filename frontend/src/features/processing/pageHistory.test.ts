import { describe, expect, it } from 'vitest';
import type { PageStepChangeSchema } from '@/api';
import { describeContent, lastStanding, stands } from '@/features/processing/pageHistory';

function change(overrides: Partial<PageStepChangeSchema> = {}): PageStepChangeSchema {
  return {
    id: 'c',
    page_id: 'page',
    stage: 'geometry',
    step_id: 'step',
    layer: 'settings',
    before: null,
    after: { max_angle: 3 },
    source: 'user',
    batch_id: null,
    undoes: null,
    undone: false,
    created_at: '2026-10-01T00:00:00Z',
    sequence: 1,
    ...overrides,
  };
}

const WORDS = { titleOf: (name: string) => `Title of ${name}`, mask: 'a mask' };

describe('stands', () => {
  it('is true for a change that nothing took back', () => {
    expect(stands(change())).toBe(true);
  });

  it('is false for a change an undo took back, and for an undo', () => {
    expect(stands(change({ undone: true }))).toBe(false);
    expect(stands(change({ source: 'undo', undoes: 'c' }))).toBe(false);
  });

  it('reads a change the server sent without the mark as standing', () => {
    const { undone: _undone, ...bare } = change();

    expect(stands(bare)).toBe(true);
  });
});

describe('lastStanding', () => {
  it('is the newest change that stands, skipping undos and what they took back', () => {
    const changes = [
      change({ id: 'undo', source: 'undo', undoes: 'second' }),
      change({ id: 'second', undone: true }),
      change({ id: 'first' }),
    ];

    expect(lastStanding(changes)?.id).toBe('first');
  });

  it('is undefined when nothing stands', () => {
    expect(lastStanding([])).toBeUndefined();
    expect(lastStanding([change({ undone: true })])).toBeUndefined();
  });
});

describe('describeContent', () => {
  it('words each field of the settings by its title, and quotes text as it is', () => {
    const text = describeContent('settings', { max_angle: 3, method: 'otsu' }, WORDS);

    expect(text).toBe('Title of max_angle: 3, Title of method: otsu');
  });

  it('is null for an empty layer', () => {
    expect(describeContent('settings', null, WORDS)).toBeNull();
    expect(describeContent('hand', null, WORDS)).toBeNull();
  });

  it('gives the shape of a manual edit as JSON, and a mask when the edit has no shape', () => {
    expect(describeContent('hand', { geometry: { degrees: 1.5 } }, WORDS)).toBe('{"degrees":1.5}');
    expect(describeContent('hand', { geometry: null }, WORDS)).toBe('a mask');
  });
});
