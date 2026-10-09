import { describe, expect, it } from 'vitest';
import type { StepPageSchema } from '@/api';
import { deskew, processor, recipe, step } from '@/features/processing/fixtures';
import { row, stepPage } from '@/features/workspace/fixtures';
import {
  barStepsOf,
  countStep,
  defaultStepOf,
  hasStepBar,
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
    step('geometry.deskew', { step_id: 'b' }),
    step('geometry.deskew', { step_id: 'c', enabled: false }),
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
  it('lists the saved steps in order with their numbers and titles', () => {
    const steps = barStepsOf(RECIPE, CATALOGUE);

    expect(steps.map(({ stepId, number, index, title }) => [stepId, number, index, title])).toEqual(
      [
        ['a', 1, 0, 'Perspective'],
        ['b', 2, 1, 'Deskew'],
        ['c', 3, 2, 'Deskew'],
        ['d', 4, 3, 'x.gone'],
      ],
    );
    expect(steps.map((entry) => entry.enabled)).toEqual([true, true, false, true]);
  });

  it('lists nothing while the recipe is read', () => {
    expect(barStepsOf(undefined, CATALOGUE)).toEqual([]);
  });
});

describe('openStepOf', () => {
  const steps = barStepsOf(RECIPE, CATALOGUE);

  it('finds the step the address names, and none when the recipe has no such step', () => {
    expect(openStepOf(steps, 'b')?.number).toBe(2);
    expect(openStepOf(steps, 'nope')).toBeNull();
    expect(openStepOf(steps, undefined)).toBeNull();
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

describe('defaultStepOf', () => {
  const steps = barStepsOf(
    recipe('r', {
      steps: [
        step('geometry.perspective', { step_id: 'a' }),
        step('geometry.deskew', { step_id: 'b' }),
        step('geometry.deskew', { step_id: 'c' }),
      ],
    }),
    CATALOGUE,
  );
  const head = { id: 'v' } as NonNullable<ReturnType<typeof row>['version']>;
  const ran = (id: string, through: number | null, status: 'fresh' | 'failed' = 'fresh') =>
    row(id, { recipe_id: 'r', version: head, through_step: through, status });

  it('is the last step when every page went through the whole recipe', () => {
    expect(defaultStepOf(steps, [ran('1', null), ran('2', null)], 'r')?.stepId).toBe('c');
  });

  it('is the last step that is on when the last step of the recipe is switched off', () => {
    const lastOff = barStepsOf(
      recipe('r', {
        steps: [
          step('geometry.perspective', { step_id: 'a' }),
          step('geometry.deskew', { step_id: 'b' }),
          step('geometry.deskew', { step_id: 'c', enabled: false }),
        ],
      }),
      CATALOGUE,
    );

    expect(defaultStepOf(lastOff, [ran('1', null)], 'r')?.stepId).toBe('b');
  });

  it('is the step the furthest run stopped at when no page went through the whole recipe', () => {
    expect(defaultStepOf(steps, [ran('1', 0), ran('2', 1), ran('3', 0)], 'r')?.stepId).toBe('b');
  });

  it('counts a page that went through the whole recipe over pages that stopped early', () => {
    expect(defaultStepOf(steps, [ran('1', 0), ran('2', null)], 'r')?.stepId).toBe('c');
  });

  it('leaves out failed pages, pages of another recipe and pages with no result', () => {
    const rows = [
      ran('1', 1),
      ran('2', null, 'failed'),
      row('3', { recipe_id: 'other', version: head, through_step: null }),
      row('4', { recipe_id: 'r', version: null, through_step: null }),
    ];

    expect(defaultStepOf(steps, rows, 'r')?.stepId).toBe('b');
  });

  it('is the last step of the recipe when the stage never ran on any page of it', () => {
    expect(defaultStepOf(steps, [], 'r')?.stepId).toBe('c');
    expect(
      defaultStepOf(steps, [row('1', { recipe_id: 'other', version: head })], 'r')?.stepId,
    ).toBe('c');
  });

  it('is nothing for a recipe with no steps', () => {
    expect(defaultStepOf([], [ran('1', null)], 'r')).toBeNull();
  });
});
