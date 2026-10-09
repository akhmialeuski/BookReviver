import { describe, expect, it } from 'vitest';
import type { ProcessorSchema } from '@/api';
import { processor, recipe, step } from '@/features/processing/fixtures';
import {
  issuesByStep,
  orderIssues,
  refusalOf,
  refusalOfAdd,
  restoreUsualOrder,
  usualPlace,
} from '@/features/processing/order';
import { draftOf, type StepDraft } from '@/features/processing/recipe';

/**
 * The order of the steps of a draft: which steps stand off the place their processors ask for, which drop would break a
 * required place, and the order that puts every step where it belongs. The processors here declare the places the
 * catalogue of the server does: B usually follows A, C must follow B, and O usually precedes A.
 */

const A_REASON = 'B reads what A leaves, so it usually comes after A.';
const C_REASON = 'C works on what B leaves, so it cannot come before B.';
const O_REASON = 'O sets the page up for A, so it usually comes before A.';

const CATALOGUE = [
  processor('geometry.a', { title: 'A' }),
  processor('geometry.b', {
    title: 'B',
    after: [{ processor_key: 'geometry.a', reason: A_REASON }],
  }),
  processor('geometry.c', {
    title: 'C',
    requires_after: [{ processor_key: 'geometry.b', reason: C_REASON }],
  }),
  processor('geometry.o', {
    title: 'O',
    before: [{ processor_key: 'geometry.a', reason: O_REASON }],
  }),
];

/** A draft of steps of the processors in the given order, which are named step-0, step-1 and so on. */
function draft(...keys: string[]): StepDraft[] {
  return draftOf(
    recipe('r', {
      steps: keys.map((key, index) => step(`geometry.${key}`, { step_id: `id-${index}` })),
    }),
  );
}

const keys = (steps: readonly StepDraft[]): string[] =>
  steps.map((entry) => entry.processorKey.replace('geometry.', ''));

describe('orderIssues', () => {
  it('finds nothing in steps that keep their places', () => {
    expect(orderIssues(draft('o', 'a', 'b', 'c'), CATALOGUE)).toEqual([]);
  });

  it('names a step that stands before the step it usually follows, with the reason and the usual kind', () => {
    expect(orderIssues(draft('b', 'a'), CATALOGUE)).toEqual([
      { stepId: 'step-0', otherId: 'step-1', kind: 'usual', reason: A_REASON },
    ]);
  });

  it('names a step that stands before the step it must follow, as a required place', () => {
    expect(orderIssues(draft('c', 'b'), CATALOGUE)).toEqual([
      { stepId: 'step-0', otherId: 'step-1', kind: 'required', reason: C_REASON },
    ]);
  });

  it('names a step that stands after the step it usually precedes, which the step that declared it carries', () => {
    expect(orderIssues(draft('a', 'o'), CATALOGUE)).toEqual([
      { stepId: 'step-1', otherId: 'step-0', kind: 'usual', reason: O_REASON },
    ]);
  });

  it('finds nothing when the other processor is not in the recipe', () => {
    expect(orderIssues(draft('c', 'a'), CATALOGUE)).toEqual([]);
  });

  it('gives a step of a processor the catalogue does not have no rules', () => {
    expect(orderIssues(draft('gone', 'b', 'gone', 'a'), CATALOGUE)).toEqual([
      { stepId: 'step-1', otherId: 'step-3', kind: 'usual', reason: A_REASON },
    ]);
  });

  it('counts a step that is off by its place, so switching a step on never makes its place wrong', () => {
    const off = draft('b', 'a').map((entry) => ({ ...entry, enabled: false }));

    expect(orderIssues(off, CATALOGUE)).toHaveLength(1);
  });

  it('names the first step of the other processor that stands on the wrong side, when there are two', () => {
    expect(orderIssues(draft('a', 'b', 'a', 'a'), CATALOGUE)).toEqual([
      { stepId: 'step-1', otherId: 'step-2', kind: 'usual', reason: A_REASON },
    ]);
  });

  it('checks each of two steps of one processor against the steps of the other', () => {
    expect(orderIssues(draft('a', 'b', 'b'), CATALOGUE)).toEqual([]);
    expect(orderIssues(draft('b', 'a', 'b'), CATALOGUE).map((issue) => issue.stepId)).toEqual([
      'step-0',
    ]);
  });
});

describe('issuesByStep', () => {
  it('groups the issues by the step that is out of place', () => {
    const grouped = issuesByStep(orderIssues(draft('c', 'b', 'a'), CATALOGUE));

    expect([...grouped.keys()]).toEqual(['step-0', 'step-1']);
    expect(grouped.get('step-0')?.map((issue) => issue.kind)).toEqual(['required']);
  });
});

describe('refusalOf', () => {
  it('refuses the drop of a step that would stand before the step it must follow', () => {
    const refusal = refusalOf(draft('b', 'c'), CATALOGUE, 'step-1', 'step-0');

    expect(refusal).toMatchObject({ stepId: 'step-1', kind: 'required', reason: C_REASON });
  });

  it('refuses the drop that carries the other step past the one that must follow it', () => {
    expect(refusalOf(draft('b', 'c'), CATALOGUE, 'step-0', 'step-1')?.reason).toBe(C_REASON);
  });

  it('allows a drop that keeps every required place', () => {
    expect(refusalOf(draft('a', 'b', 'c'), CATALOGUE, 'step-0', 'step-1')).toBeUndefined();
  });

  it('allows a drop that only takes a step off its usual place', () => {
    expect(refusalOf(draft('a', 'b'), CATALOGUE, 'step-1', 'step-0')).toBeUndefined();
  });

  it('does not count a required place the draft already breaks, so another step can still be moved', () => {
    const broken = draft('c', 'b', 'a');

    expect(refusalOf(broken, CATALOGUE, 'step-2', 'step-1')).toBeUndefined();
    expect(refusalOf(broken, CATALOGUE, 'step-1', 'step-0')).toBeUndefined();
  });
});

const processorOf = (key: string): ProcessorSchema => {
  const found = CATALOGUE.find((entry) => entry.key === `geometry.${key}`);
  if (found === undefined) {
    throw new Error(`No processor ${key}`);
  }
  return found;
};

describe('refusalOfAdd', () => {
  it('refuses a step added where it would stand after the step that must follow it', () => {
    const refusal = refusalOfAdd(draft('c'), CATALOGUE, processorOf('b'), 1);

    expect(refusal).toMatchObject({ kind: 'required', reason: C_REASON });
  });

  it('allows the place that keeps every required place', () => {
    expect(refusalOfAdd(draft('c'), CATALOGUE, processorOf('b'), 0)).toBeUndefined();
  });

  it('does not count a required place the draft already breaks', () => {
    expect(refusalOfAdd(draft('c', 'b'), CATALOGUE, processorOf('b'), 2)).toBeUndefined();
  });
});

describe('usualPlace', () => {
  it('puts a step at the end when no rule asks for another place', () => {
    expect(usualPlace(draft('a'), CATALOGUE, processorOf('b'))).toBe(1);
    expect(usualPlace([], CATALOGUE, processorOf('a'))).toBe(0);
  });

  it('puts a second step of a processor after the first and before the step that follows it', () => {
    expect(usualPlace(draft('a', 'b'), CATALOGUE, processorOf('a'))).toBe(1);
  });

  it('puts a step before the step that must follow it', () => {
    expect(usualPlace(draft('c'), CATALOGUE, processorOf('b'))).toBe(0);
  });

  it('puts a step before the step it usually precedes', () => {
    expect(usualPlace(draft('a'), CATALOGUE, processorOf('o'))).toBe(0);
  });

  it('moves no step that is already in the draft', () => {
    const before = draft('b', 'a');

    usualPlace(before, CATALOGUE, processorOf('a'));

    expect(keys(before)).toEqual(['b', 'a']);
  });
});

describe('restoreUsualOrder', () => {
  it('puts every step where its processor asks for, whether the place is usual or required', () => {
    expect(keys(restoreUsualOrder(draft('c', 'b', 'a', 'o'), CATALOGUE))).toEqual([
      'o',
      'a',
      'b',
      'c',
    ]);
  });

  it('keeps the settings, the switches and the identities of the steps', () => {
    const steps = draft('b', 'a').map((entry, index) => ({
      ...entry,
      params: { index },
      enabled: index === 0,
    }));

    const restored = restoreUsualOrder(steps, CATALOGUE);

    expect(restored).toEqual([steps[1], steps[0]]);
  });

  it('leaves steps that stand where they should in the order they have', () => {
    const steps = draft('a', 'gone', 'b', 'x', 'c');

    expect(keys(restoreUsualOrder(steps, CATALOGUE))).toEqual(['a', 'gone', 'b', 'x', 'c']);
  });

  it('moves a step no further than its rules need', () => {
    expect(keys(restoreUsualOrder(draft('b', 'x', 'a'), CATALOGUE))).toEqual(['x', 'a', 'b']);
  });

  it('puts the steps of one processor after every step of the processor they follow', () => {
    const restored = restoreUsualOrder(draft('b', 'a', 'b', 'a'), CATALOGUE);

    expect(keys(restored)).toEqual(['a', 'a', 'b', 'b']);
    expect(orderIssues(restored, CATALOGUE)).toEqual([]);
  });

  it('leaves the rest in their order when the rules cannot all be met', () => {
    const cycle = [
      processor('geometry.p', {
        after: [{ processor_key: 'geometry.q', reason: 'P follows Q.' }],
      }),
      processor('geometry.q', {
        after: [{ processor_key: 'geometry.p', reason: 'Q follows P.' }],
      }),
    ];

    expect(keys(restoreUsualOrder(draft('p', 'q'), cycle))).toEqual(['p', 'q']);
  });
});
