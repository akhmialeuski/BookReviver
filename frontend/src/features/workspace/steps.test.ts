import { describe, expect, it } from 'vitest';
import type { StepPageSchema } from '@/api';
import { deskew, processor, recipe, step } from '@/features/processing/fixtures';
import { row, stepPage } from '@/features/workspace/fixtures';
import {
  type BarStep,
  barStepsOf,
  countStep,
  hasStepBar,
  markOfCondition,
  neighboursOf,
  openStepOf,
} from '@/features/workspace/steps';

/** The steps of the open recipe as the bar reads them, and the counts of what a step did on the pages of the book. */

const CATALOGUE = [
  processor('geometry.perspective', { title: 'Perspective' }),
  deskew({ title: 'Deskew' }),
];

const RECIPE = recipe('r', {
  steps: [
    step('geometry.perspective', { step_id: 'a' }),
    step('geometry.deskew', { step_id: 'b', applies_to: 'text' }),
    step('geometry.deskew', { step_id: 'c', applies_to: 'pictures', enabled: false }),
    step('x.gone', { step_id: 'd' }),
  ],
});

function placed(
  state: StepPageSchema['state'],
  flags: StepPageSchema['flags'] = [],
): StepPageSchema {
  return stepPage('b', state, { flags });
}

describe('barStepsOf', () => {
  it('lists the saved steps in order with their numbers, titles and conditions', () => {
    const steps = barStepsOf(RECIPE, CATALOGUE);

    expect(steps.map(({ stepId, number, index, title }) => [stepId, number, index, title])).toEqual(
      [
        ['a', 1, 0, 'Perspective'],
        ['b', 2, 1, 'Deskew'],
        ['c', 3, 2, 'Deskew'],
        ['d', 4, 3, 'x.gone'],
      ],
    );
    expect(steps.map((entry) => entry.appliesTo)).toEqual(['all', 'text', 'pictures', 'all']);
    expect(steps.map((entry) => entry.enabled)).toEqual([true, true, false, true]);
  });

  it('lists nothing while the recipe is read', () => {
    expect(barStepsOf(undefined, CATALOGUE)).toEqual([]);
  });
});

describe('markOfCondition', () => {
  it.each([
    ['all', null],
    ['text', 'text'],
    ['pictures', 'picture'],
    ['color-pictures', 'picture'],
    ['bw-pictures', 'picture'],
  ] as const)('marks the condition %s as %s', (condition, mark) => {
    expect(markOfCondition(condition)).toBe(mark);
  });
});

describe('openStepOf and neighboursOf', () => {
  const steps = barStepsOf(RECIPE, CATALOGUE);
  const at = (index: number): BarStep => {
    const found = steps[index];
    if (found === undefined) {
      throw new Error(`The recipe has no step ${index}`);
    }
    return found;
  };

  it('finds the step the address names, and none when the recipe has no such step', () => {
    expect(openStepOf(steps, 'b')?.number).toBe(2);
    expect(openStepOf(steps, 'nope')).toBeNull();
    expect(openStepOf(steps, undefined)).toBeNull();
  });

  it('gives the steps either side, counting the ones that are off', () => {
    expect(neighboursOf(steps, at(1))).toEqual({ previous: at(0), next: at(2) });
    expect(neighboursOf(steps, at(0)).previous).toBeNull();
    expect(neighboursOf(steps, at(3)).next).toBeNull();
  });
});

describe('countStep', () => {
  it('counts the pages by what the step did on them and by the flags the server raised', () => {
    const rows = [
      row('1', { step: placed('found') }),
      row('2', { step: placed('found', ['unusual']) }),
      row('3', { step: placed('by-hand', ['by-hand']) }),
      row('4', { step: placed('skipped', ['skipped']) }),
      row('5', { step: placed('default') }),
    ];

    expect(countStep(rows)).toEqual({
      found: 2,
      byHand: 1,
      skipped: 1,
      notRun: 1,
      check: 0,
      unusual: 1,
    });
  });

  it('counts a page with a setting of its own as set by hand, though the step found its shape', () => {
    const counts = countStep([row('1', { step: placed('found', ['by-hand']) })]);

    expect(counts.found).toBe(1);
    expect(counts.byHand).toBe(1);
  });

  it('counts the pages the step was unsure of by the flag, whichever step marked them first', () => {
    const rows = [
      row('1', { step: placed('found', ['unsure']) }),
      row('2', { step: placed('skipped', ['skipped']) }),
      row('3', {
        step: placed('found'),
        review: 'low-confidence',
        review_processor: 'geometry.crop',
      }),
    ];

    expect(countStep(rows).check).toBe(1);
  });

  it('leaves out a row that was not asked for a step', () => {
    expect(countStep([row('1')])).toEqual({
      found: 0,
      byHand: 0,
      skipped: 0,
      notRun: 0,
      check: 0,
      unusual: 0,
    });
  });
});

describe('hasStepBar', () => {
  it('is for the Geometry and the Cleanup stages, and not for the others', () => {
    expect(hasStepBar('geometry')).toBe(true);
    expect(hasStepBar('cleanup')).toBe(true);
    expect(hasStepBar('page-split')).toBe(false);
    expect(hasStepBar('recognition')).toBe(false);
  });
});
