import { describe, expect, it } from 'vitest';
import { PLACEMENT_KEY } from '@/features/editors/placement';
import { hasManualMargins, withMarginsSource } from '@/features/processing/margins';
import { type StepDraft, setStepParams } from '@/features/processing/recipe';

/**
 * The margins of the normalize step: a margin the reader changes in the form switches the step to margins set by
 * hand, which measuring the book leaves as they are, and nothing else does.
 */

const MEASURED = {
  margins_source: 'measured',
  margin_top: 40,
  margin_bottom: 50,
  margin_inner: 60,
  margin_outer: 70,
  page_width: 900,
  line_height: 20,
};

function draft(processorKey: string, params: Record<string, unknown>): StepDraft[] {
  return [{ id: 'step-0', stepId: 'id-step', processorKey, params, enabled: true }];
}

describe('withMarginsSource', () => {
  it.each(['margin_top', 'margin_bottom', 'margin_inner', 'margin_outer'])(
    'switches to manual margins when %s is changed',
    (name) => {
      const changed = withMarginsSource(PLACEMENT_KEY, MEASURED, { ...MEASURED, [name]: 99 });

      expect(changed).toEqual({ ...MEASURED, [name]: 99, margins_source: 'manual' });
      expect(hasManualMargins(changed)).toBe(true);
    },
  );

  it('leaves the source alone when a setting that is no margin is changed', () => {
    const changed = withMarginsSource(PLACEMENT_KEY, MEASURED, { ...MEASURED, page_width: 1000 });

    expect(changed.margins_source).toBe('measured');
  });

  it('takes the source as the reader set it when the source and a margin change together', () => {
    const manual = { ...MEASURED, margins_source: 'manual' };
    const changed = withMarginsSource(PLACEMENT_KEY, manual, {
      ...manual,
      margins_source: 'measured',
      margin_top: 12,
    });

    expect(changed.margins_source).toBe('measured');
  });

  it('keeps manual margins manual when the reader edits another margin', () => {
    const manual = { ...MEASURED, margins_source: 'manual' };

    expect(
      withMarginsSource(PLACEMENT_KEY, manual, { ...manual, margin_outer: 1 }).margins_source,
    ).toBe('manual');
  });

  it('does not touch a step of another processor', () => {
    const changed = withMarginsSource('geometry.crop', MEASURED, { ...MEASURED, margin_top: 99 });

    expect(changed.margins_source).toBe('measured');
  });
});

describe('setStepParams on the normalize step', () => {
  it('marks the margins as set by hand when the form changes one', () => {
    const [step] = setStepParams(draft(PLACEMENT_KEY, MEASURED), 'step-0', {
      ...MEASURED,
      margin_inner: 5,
    });

    expect(step?.params.margins_source).toBe('manual');
    expect(step?.params.margin_inner).toBe(5);
  });
});
