import { describe, expect, it } from 'vitest';
import { deskew, recipe, spread, step, whole } from '@/features/processing/fixtures';
import {
  addStep,
  bodyOf,
  canPreview,
  draftOf,
  moveStep,
  pagesToGoStale,
  removeStep,
  sameAsSaved,
  setStepCondition,
  setStepParams,
  toggleStep,
} from '@/features/processing/recipe';
import { row } from '@/features/workspace/fixtures';

const TWO_STEPS = recipe('r1', {
  steps: [
    step('geometry.deskew', { params: { max_angle: 5 } }),
    step('geometry.crop', { enabled: false }),
  ],
});

describe('draftOf and bodyOf', () => {
  it('gives each step a place in the draft, and sends the identifier and the condition the server gave it', () => {
    const draft = draftOf(TWO_STEPS);

    expect(draft.map((entry) => entry.id)).toEqual(['step-0', 'step-1']);
    expect(bodyOf(draft)).toEqual([
      {
        processor_key: 'geometry.deskew',
        params: { max_angle: 5 },
        enabled: true,
        step_id: 'id-geometry.deskew',
        applies_to: 'all',
      },
      {
        processor_key: 'geometry.crop',
        params: {},
        enabled: false,
        step_id: 'id-geometry.crop',
        applies_to: 'all',
      },
    ]);
  });

  it('keeps the identifier of a step as it is moved, and sends none for a step that was added', () => {
    const moved = moveStep(draftOf(TWO_STEPS), 'step-0', 'step-1');
    const added = addStep(moved, deskew());

    expect(bodyOf(added).map((entry) => entry.step_id)).toEqual([
      'id-geometry.crop',
      'id-geometry.deskew',
      null,
    ]);
  });

  it('copies the parameters, so a change of the draft leaves the saved recipe alone', () => {
    const draft = draftOf(TWO_STEPS);

    Object.assign(draft[0]?.params ?? {}, { max_angle: 9 });

    expect(TWO_STEPS.steps[0]?.params).toEqual({ max_angle: 5 });
  });
});

describe('moveStep', () => {
  it('puts a step where the step it was dropped on stands', () => {
    const moved = moveStep(draftOf(TWO_STEPS), 'step-0', 'step-1');

    expect(moved.map((entry) => entry.processorKey)).toEqual(['geometry.crop', 'geometry.deskew']);
  });

  it('leaves the order when a step is dropped on itself or on nothing', () => {
    const draft = draftOf(TWO_STEPS);

    expect(moveStep(draft, 'step-0', 'step-0')).toEqual(draft);
    expect(moveStep(draft, 'step-0', 'missing')).toEqual(draft);
  });
});

describe('toggleStep, removeStep and setStepParams', () => {
  it('switches a step off and on again, keeping its parameters', () => {
    const off = toggleStep(draftOf(TWO_STEPS), 'step-0');

    expect(off[0]?.enabled).toBe(false);
    expect(off[0]?.params).toEqual({ max_angle: 5 });
    expect(toggleStep(off, 'step-0')[0]?.enabled).toBe(true);
  });

  it('takes a step out', () => {
    expect(removeStep(draftOf(TWO_STEPS), 'step-0').map((entry) => entry.id)).toEqual(['step-1']);
  });

  it('replaces the parameters of one step only', () => {
    const changed = setStepParams(draftOf(TWO_STEPS), 'step-1', { margin: 3 });

    expect(changed[0]?.params).toEqual({ max_angle: 5 });
    expect(changed[1]?.params).toEqual({ margin: 3 });
  });
});

describe('setStepCondition', () => {
  it('changes which pages one step processes, and makes the draft differ from the saved recipe', () => {
    const changed = setStepCondition(draftOf(TWO_STEPS), 'step-0', 'pictures');

    expect(changed.map((entry) => entry.appliesTo)).toEqual(['pictures', 'all']);
    expect(sameAsSaved(TWO_STEPS, changed)).toBe(false);
  });

  it('reads the condition a step was saved with', () => {
    const saved = recipe('r', { steps: [step('geometry.deskew', { applies_to: 'text' })] });

    expect(draftOf(saved)[0]?.appliesTo).toBe('text');
    expect(sameAsSaved(saved, draftOf(saved))).toBe(true);
  });
});

describe('addStep', () => {
  it('adds the step at the end with the defaults of its schema and an identity not yet used', () => {
    const draft = removeStep(draftOf(TWO_STEPS), 'step-0');

    const added = addStep(draft, deskew());

    expect(added.map((entry) => entry.id)).toEqual(['step-1', 'step-2']);
    expect(added[1]).toMatchObject({
      processorKey: 'geometry.deskew',
      params: { max_angle: 5, min_confidence: 0.3 },
      enabled: true,
    });
  });

  it('starts a draft that is empty', () => {
    expect(addStep([], deskew())[0]?.id).toBe('step-0');
  });
});

describe('sameAsSaved', () => {
  it('is true for the draft just made from the recipe', () => {
    expect(sameAsSaved(TWO_STEPS, draftOf(TWO_STEPS))).toBe(true);
  });

  it('does not mind the order of the keys of the parameters', () => {
    const saved = recipe('r', { steps: [step('geometry.deskew', { params: { a: 1, b: 2 } })] });
    const draft = draftOf(saved).map((entry) => ({ ...entry, params: { b: 2, a: 1 } }));

    expect(sameAsSaved(saved, draft)).toBe(true);
  });

  it('sees a changed value, a switched step, a removed step and a new order', () => {
    const draft = draftOf(TWO_STEPS);

    expect(sameAsSaved(TWO_STEPS, setStepParams(draft, 'step-0', { max_angle: 6 }))).toBe(false);
    expect(sameAsSaved(TWO_STEPS, toggleStep(draft, 'step-1'))).toBe(false);
    expect(sameAsSaved(TWO_STEPS, removeStep(draft, 'step-1'))).toBe(false);
    expect(sameAsSaved(TWO_STEPS, moveStep(draft, 'step-0', 'step-1'))).toBe(false);
  });
});

describe('pagesToGoStale', () => {
  it('counts the pages the recipe has processed that are up to date', () => {
    const rows = [
      row('a', { recipe_id: 'r1', status: 'fresh' }),
      row('b', { recipe_id: 'r1', status: 'fresh' }),
      row('c', { recipe_id: 'r1', status: 'stale' }),
      row('d', { recipe_id: 'r2', status: 'fresh' }),
      row('e', { recipe_id: null, status: 'not-run' }),
    ];

    expect(pagesToGoStale(rows, 'r1')).toBe(2);
  });
});

describe('canPreview', () => {
  const catalogue = [deskew(), spread(), whole()];

  it('allows the steps of a processor that makes one image of a page', () => {
    expect(canPreview(draftOf(TWO_STEPS).slice(0, 1), catalogue, 0)).toBe(true);
  });

  it('refuses a step that cuts a scan into pages', () => {
    const steps = draftOf(recipe('s', { steps: [step('split.spread')] }));

    expect(canPreview(steps, catalogue, 0)).toBe(false);
  });

  it('refuses when every step up to the one asked for is off', () => {
    const steps = toggleStep(draftOf(recipe('s', { steps: [step('geometry.deskew')] })), 'step-0');

    expect(canPreview(steps, catalogue, 0)).toBe(false);
  });

  it('refuses a step whose processor the catalogue does not know', () => {
    expect(canPreview(draftOf(recipe('s', { steps: [step('x.y')] })), catalogue, 0)).toBe(false);
  });
});
