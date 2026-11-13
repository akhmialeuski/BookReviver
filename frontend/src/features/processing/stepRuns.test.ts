import { describe, expect, it } from 'vitest';
import { row } from '@/features/pages/fixtures';
import { recipe, step, version } from '@/features/processing/fixtures';
import {
  canRunThrough,
  passedPages,
  stepOfProcessor,
  versionOfStep,
} from '@/features/processing/stepRuns';

describe('passedPages', () => {
  const three = recipe('r', {
    steps: [
      step('geometry.perspective', { step_id: 'a' }),
      step('geometry.deskew', { step_id: 'b' }),
      step('geometry.crop', { step_id: 'c' }),
    ],
  });
  const rows = [
    row('whole', { recipe_id: 'r' }),
    row('first', { recipe_id: 'r', through_step_id: 'a' }),
    row('second', { recipe_id: 'r', through_step_id: 'b' }),
    row('stale', { recipe_id: 'r', status: 'stale', through_step_id: 'b' }),
    row('failed', { recipe_id: 'r', status: 'failed', through_step_id: 'c' }),
    row('other', { recipe_id: 'x' }),
    row('never', { status: 'not-run' }),
  ];

  it('counts every page that has a result by the recipe for the first step', () => {
    expect(passedPages(rows, three, 0)).toBe(4);
  });

  it('leaves out the pages that stopped before a step', () => {
    expect(passedPages(rows, three, 1)).toBe(3);
    expect(passedPages(rows, three, 2)).toBe(1);
  });

  it('finds the step a page stopped at by its identifier, wherever the step stands now', () => {
    const moved = recipe('r', {
      steps: [
        step('geometry.deskew', { step_id: 'b' }),
        step('geometry.perspective', { step_id: 'a' }),
        step('geometry.crop', { step_id: 'c' }),
      ],
    });

    // The pages that stopped at b stopped at the first step now, and the one that stopped at a passed the second

    expect(passedPages(rows, moved, 1)).toBe(2);
  });

  it('counts none of the pages another recipe processed, the ones that failed and the ones not run', () => {
    expect(passedPages(rows, { ...three, id: 'nobody' }, 0)).toBe(0);
  });
});

describe('canRunThrough', () => {
  it('is true when the step or one before it is on', () => {
    const steps = [{ enabled: false }, { enabled: true }, { enabled: false }];

    expect([0, 1, 2].map((index) => canRunThrough(steps, index))).toEqual([false, true, true]);
  });
});

describe('stepOfProcessor', () => {
  const stored = recipe('r', { steps: [step('a'), step('b'), step('a')] });

  it('finds the first step that runs the processor', () => {
    expect(stepOfProcessor(stored, 'a')).toBe(0);
    expect(stepOfProcessor(stored, 'b')).toBe(1);
  });

  it('is null for a processor the recipe does not run and for no recipe', () => {
    expect(stepOfProcessor(stored, 'c')).toBeNull();
    expect(stepOfProcessor(undefined, 'a')).toBeNull();
  });
});

describe('versionOfStep', () => {
  const chain = [version('v1'), version('v2')];
  const steps = [{ enabled: true }, { enabled: false }, { enabled: true }];

  it('counts only the steps that are on to find the place in the chain', () => {
    expect(versionOfStep(chain, steps, 0)?.id).toBe('v1');
    expect(versionOfStep(chain, steps, 2)?.id).toBe('v2');
  });

  it('is null for a step that is off and for a step the page has not reached', () => {
    expect(versionOfStep(chain, steps, 1)).toBeNull();
    expect(versionOfStep([version('v1')], steps, 2)).toBeNull();
  });
});
