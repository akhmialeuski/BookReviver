import { describe, expect, it } from 'vitest';
import type { PageStepSettingsSchema } from '@/api';
import { changedFields, effectiveParams, pageValuesOf } from '@/features/processing/pageSettings';

/** What the open page changes for a step, read from the settings the server lists, and the values a form changed. */

function settings(stepId: string, params: Record<string, unknown>): PageStepSettingsSchema {
  return {
    page_id: 'page',
    stage: 'geometry',
    step_id: stepId,
    params,
    updated_at: '2026-10-03T10:00:00Z',
  };
}

describe('pageValuesOf', () => {
  const listed = [settings('a', { max_angle: 3 }), settings('b', { min_confidence: 0.5 })];

  it('finds the fields the page changes for the step', () => {
    expect(pageValuesOf(listed, 'b')).toEqual({ min_confidence: 0.5 });
  });

  it('gives nothing for a step the page changes nothing of, and for a step that is not saved', () => {
    expect(pageValuesOf(listed, 'c')).toEqual({});
    expect(pageValuesOf(listed, null)).toEqual({});
  });

  it('gives nothing before the settings are read', () => {
    expect(pageValuesOf(undefined, 'a')).toEqual({});
  });
});

describe('effectiveParams', () => {
  it('lays the fields of the page over the parameters of the recipe and leaves the rest', () => {
    expect(effectiveParams({ max_angle: 5, min_confidence: 0.3 }, { max_angle: 3 })).toEqual({
      max_angle: 3,
      min_confidence: 0.3,
    });
  });
});

describe('changedFields', () => {
  it('lists the fields whose value is not what it was', () => {
    expect(
      changedFields({ max_angle: 5, min_confidence: 0.3 }, { max_angle: 7, min_confidence: 0.3 }),
    ).toEqual([['max_angle', 7]]);
  });

  it('compares nested values by what they hold', () => {
    expect(changedFields({ zone: { a: 1 } }, { zone: { a: 1 } })).toEqual([]);
    expect(changedFields({ zone: { a: 1 } }, { zone: { a: 2 } })).toEqual([['zone', { a: 2 }]]);
  });

  it('lists a field the form did not start with', () => {
    expect(changedFields({}, { max_angle: 3 })).toEqual([['max_angle', 3]]);
  });
});
