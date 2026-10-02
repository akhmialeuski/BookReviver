import { describe, expect, it } from 'vitest';
import { recipe, step, version } from '@/features/processing/fixtures';
import {
  canRunThrough,
  passedPages,
  stepOfProcessor,
  versionOfStep,
} from '@/features/processing/stepRuns';
import { row } from '@/features/workspace/fixtures';

describe('passedPages', () => {
  const rows = [
    row('whole', { recipe_id: 'r' }),
    row('first', { recipe_id: 'r', through_step: 0 }),
    row('second', { recipe_id: 'r', through_step: 1 }),
    row('stale', { recipe_id: 'r', status: 'stale', through_step: 1 }),
    row('failed', { recipe_id: 'r', status: 'failed', through_step: 2 }),
    row('other', { recipe_id: 'x' }),
    row('never', { status: 'not-run' }),
  ];

  it('counts every page that has a result by the recipe for the first step', () => {
    expect(passedPages(rows, 'r', 0)).toBe(4);
  });

  it('leaves out the pages that stopped before a step', () => {
    expect(passedPages(rows, 'r', 1)).toBe(3);
    expect(passedPages(rows, 'r', 2)).toBe(1);
  });

  it('counts none of the pages another recipe processed, the ones that failed and the ones not run', () => {
    expect(passedPages(rows, 'nobody', 0)).toBe(0);
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
