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
    scope: 'pages',
    group_label: '',
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

const titleOf = (name: string): string => `Title of ${name}`;

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
    const text = describeContent('settings', { max_angle: 3, method: 'otsu' }, titleOf);

    expect(text).toBe('Title of max_angle: 3, Title of method: otsu');
  });

  it('is null for an empty layer', () => {
    expect(describeContent('settings', null, titleOf)).toBeNull();
    expect(describeContent('hand', null, titleOf)).toBeNull();
  });

  describe('of a manual edit', () => {
    /** A snapshot of the hand layer as the server writes it. */
    function hand(kind: string, geometry: unknown): Record<string, unknown> {
      return { kind, geometry, mask_key: null, edit_hash: 'abc' };
    }

    const describeHand = (kind: string, geometry: unknown): string | null =>
      describeContent('hand', hand(kind, geometry), titleOf);

    it('words a frame and a content box by their place and size in whole pixels', () => {
      const frame = { left: 97.70357142857144, top: 87.4, width: 1503.6, height: 2100.2 };

      expect(describeHand('rect', frame)).toBe('Frame: left 98, top 87, 1504 × 2100 px');
      expect(describeHand('content-box', frame)).toBe('Frame: left 98, top 87, 1504 × 2100 px');
    });

    it('words a line and the cut of a split by its two ends in whole pixels', () => {
      const line = { start: { x: 10.4, y: 0 }, end: { x: 12.6, y: 600.5 } };

      expect(describeHand('line', line)).toBe('Line from (10, 0) to (13, 601) px');
      expect(describeHand('split', { pages: 2, line })).toBe('Line from (10, 0) to (13, 601) px');
    });

    it('words a rotation by its angle to one decimal', () => {
      expect(describeHand('rotation', { degrees: 1.5 })).toBe('Angle 1.5°');
      expect(describeHand('rotation', { degrees: -2.449 })).toBe('Angle -2.4°');
      expect(describeHand('rotation', { degrees: 2 })).toBe('Angle 2.0°');
    });

    it('says only that four corners were set for a quadrilateral', () => {
      const corner = { x: 1, y: 2 };

      expect(
        describeHand('quad', {
          top_left: corner,
          top_right: corner,
          bottom_right: corner,
          bottom_left: corner,
        }),
      ).toBe('Four corners set by hand');
    });

    it('says only that a mesh was set for a mesh', () => {
      const node = { x: 1, y: 2 };

      expect(
        describeHand('mesh', {
          rows: [
            [node, node],
            [node, node],
          ],
        }),
      ).toBe('Mesh set by hand');
    });

    it('says a mask was painted for a brush mask, whether or not it kept the strokes', () => {
      expect(
        describeHand('brush-mask', { strokes: [{ radius: 5, points: [{ x: 1, y: 1 }] }] }),
      ).toBe('Mask painted by hand');
      expect(describeHand('brush-mask', null)).toBe('Mask painted by hand');
    });

    it('counts the regions, in the singular for one', () => {
      const zone = (mode: string) => ({
        mode,
        points: [
          { x: 0, y: 0 },
          { x: 5, y: 0 },
          { x: 5, y: 5 },
        ],
      });

      expect(describeHand('regions', { zones: [zone('add'), zone('remove')] })).toBe('2 regions');
      expect(describeHand('regions', { zones: [zone('add')] })).toBe('1 region');
      expect(describeHand('regions', { zones: [] })).toBe('0 regions');
    });

    it('says it was set by hand for a kind it does not know', () => {
      expect(describeHand('hologram', { degrees: 1 })).toBe('Set by hand');
      expect(describeContent('hand', { geometry: { degrees: 1 } }, titleOf)).toBe('Set by hand');
      expect(describeHand('none', null)).toBe('Set by hand');
    });

    it('says it was set by hand when the shape does not fit its kind', () => {
      expect(describeHand('rect', { degrees: 1 })).toBe('Set by hand');
      expect(describeHand('rect', { left: 1, top: 1, width: 0, height: 5 })).toBe('Set by hand');
      expect(describeHand('line', null)).toBe('Set by hand');
      expect(describeHand('split', { pages: 1, line: null })).toBe('Set by hand');
      expect(describeHand('rotation', 'text')).toBe('Set by hand');
      expect(describeHand('quad', { top_left: { x: 1, y: 1 } })).toBe('Set by hand');
      expect(describeHand('mesh', { rows: [] })).toBe('Set by hand');
      expect(describeHand('regions', { zones: [{ mode: 'add', points: [] }] })).toBe('Set by hand');
    });
  });
});
