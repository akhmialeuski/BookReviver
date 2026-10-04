import type { OrderRuleKind, ProcessorSchema } from '@/api';
import { addStep, moveStep, type StepDraft } from '@/features/processing/recipe';

/**
 * The order of the steps of a draft, as the processors ask for it in the catalogue.
 *
 * A processor says where its step usually stands (`after`, `before`) and where it must stand (`requires_after`). The
 * server checks the order when the recipe is saved, and these functions read the same declarations so the panel can mark
 * a step and refuse a place while the reader drags, before anything is sent. A rule names processors, and a recipe may
 * hold two steps of one processor, so a rule is broken when any step of the other processor stands on the wrong side, and
 * the first such step is the one the issue names.
 */

/** A step that stands where its processor does not want it. */
export interface OrderIssue {
  /** The draft identity of the step that is out of place, which is the one whose processor declared the rule. */
  stepId: string;
  /** The draft identity of the step it is compared with. */
  otherId: string;
  /** Whether the place is the usual one, which the step may leave, or a required one. */
  kind: OrderRuleKind;
  /** One sentence that says why the place matters. */
  reason: string;
}

/** One rule of a processor, with whether the other step has to stand before this one. */
interface Rule {
  processorKey: string;
  reason: string;
  kind: OrderRuleKind;
  otherFirst: boolean;
}

function rulesOf(processor: ProcessorSchema): Rule[] {
  return [
    ...processor.after.map((rule) => ({
      processorKey: rule.processor_key,
      reason: rule.reason,
      kind: 'usual' as const,
      otherFirst: true,
    })),
    ...processor.requires_after.map((rule) => ({
      processorKey: rule.processor_key,
      reason: rule.reason,
      kind: 'required' as const,
      otherFirst: true,
    })),
    ...processor.before.map((rule) => ({
      processorKey: rule.processor_key,
      reason: rule.reason,
      kind: 'usual' as const,
      otherFirst: false,
    })),
  ];
}

/**
 * Find the steps that stand where their processor does not want them.
 *
 * @param steps The draft, whether its steps are on or off.
 * @param catalogue The processors of the stage. A step whose processor is not in it has no rules.
 * @returns One issue for each broken rule of each step, in the order of the steps.
 */
export function orderIssues(
  steps: readonly StepDraft[],
  catalogue: readonly ProcessorSchema[],
): OrderIssue[] {
  const issues: OrderIssue[] = [];
  steps.forEach((step, index) => {
    const processor = catalogue.find((entry) => entry.key === step.processorKey);
    for (const rule of processor === undefined ? [] : rulesOf(processor)) {
      const wrong = steps.findIndex(
        (other, place) =>
          other.processorKey === rule.processorKey && place > index === rule.otherFirst,
      );
      const other = steps[wrong];
      if (other !== undefined) {
        issues.push({ stepId: step.id, otherId: other.id, kind: rule.kind, reason: rule.reason });
      }
    }
  });
  return issues;
}

/** Group the issues by the step that is out of place. */
export function issuesByStep(issues: readonly OrderIssue[]): Map<string, OrderIssue[]> {
  const grouped = new Map<string, OrderIssue[]>();
  for (const issue of issues) {
    grouped.set(issue.stepId, [...(grouped.get(issue.stepId) ?? []), issue]);
  }
  return grouped;
}

function identityOf(issue: OrderIssue): string {
  return `${issue.stepId}|${issue.otherId}|${issue.reason}`;
}

/**
 * Find the required place that a change of the draft makes it break, if there is one.
 *
 * A required place the draft already breaks is not counted, so a recipe that was saved in the free order can still be
 * rearranged in the usual one, and only a change that makes things worse is refused.
 */
function newRequiredIssue(
  before: readonly StepDraft[],
  after: readonly StepDraft[],
  catalogue: readonly ProcessorSchema[],
): OrderIssue | undefined {
  const broken = new Set(
    orderIssues(before, catalogue)
      .filter((issue) => issue.kind === 'required')
      .map(identityOf),
  );
  return orderIssues(after, catalogue).find(
    (issue) => issue.kind === 'required' && !broken.has(identityOf(issue)),
  );
}

/**
 * Find the required place that dropping a step on another step would make the draft break, if there is one.
 *
 * @param steps The draft.
 * @param catalogue The processors of the stage.
 * @param activeId The step being dragged.
 * @param overId The step it is over, whose place it would take.
 */
export function refusalOf(
  steps: readonly StepDraft[],
  catalogue: readonly ProcessorSchema[],
  activeId: string,
  overId: string,
): OrderIssue | undefined {
  return newRequiredIssue(steps, moveStep(steps, activeId, overId), catalogue);
}

/**
 * Find the required place that adding a step of a processor at a place would make the draft break, if there is one.
 *
 * @param steps The draft.
 * @param catalogue The processors of the stage.
 * @param processor The processor of the step to add.
 * @param index The place the step would take, from zero.
 */
export function refusalOfAdd(
  steps: readonly StepDraft[],
  catalogue: readonly ProcessorSchema[],
  processor: ProcessorSchema,
  index: number,
): OrderIssue | undefined {
  return newRequiredIssue(steps, addStep(steps, processor, index), catalogue);
}

/** How much a broken rule weighs when the places for a new step are compared: a required place far outweighs a usual one. */
const WEIGHT: Readonly<Record<OrderRuleKind, number>> = { usual: 1, required: 100 };

/**
 * Find where a new step of a processor usually stands among the steps of the draft, without moving any of them.
 *
 * Each place is tried, and the one where the new step breaks the fewest rules, the required ones counting most, is taken.
 * Of equal places the latest is taken, so a step that nothing asks to stand earlier goes to the end, and a second step of
 * a processor goes after the steps of the processor it already has.
 *
 * @param steps The draft.
 * @param catalogue The processors of the stage.
 * @param processor The processor of the step to add.
 * @returns The place of the new step, from zero, which is the end when no rule asks for another.
 */
export function usualPlace(
  steps: readonly StepDraft[],
  catalogue: readonly ProcessorSchema[],
  processor: ProcessorSchema,
): number {
  let best = steps.length;
  let fewest = Number.POSITIVE_INFINITY;
  for (let index = steps.length; index >= 0; index -= 1) {
    const trial = addStep(steps, processor, index);
    const added = trial[index]?.id;
    const cost = orderIssues(trial, catalogue)
      .filter((issue) => issue.stepId === added || issue.otherId === added)
      .reduce((total, issue) => total + WEIGHT[issue.kind], 0);
    if (cost < fewest) {
      fewest = cost;
      best = index;
    }
  }
  return best;
}

/**
 * Put the steps in the order their processors ask for, which changes nothing but their places.
 *
 * Each step is taken from the draft in turn, the first that has no step left to come before it, so the steps that
 * stand where they should keep their relative order. Rules that cannot all be met, which a catalogue should not have,
 * leave the rest of the steps in the order they were.
 *
 * @param steps The draft.
 * @param catalogue The processors of the stage.
 */
export function restoreUsualOrder(
  steps: readonly StepDraft[],
  catalogue: readonly ProcessorSchema[],
): StepDraft[] {
  const before = new Map<string, Set<string>>(steps.map((step) => [step.id, new Set<string>()]));
  for (const step of steps) {
    const processor = catalogue.find((entry) => entry.key === step.processorKey);
    for (const rule of processor === undefined ? [] : rulesOf(processor)) {
      for (const other of steps.filter(
        (entry) => entry.processorKey === rule.processorKey && entry.id !== step.id,
      )) {
        before.get(rule.otherFirst ? step.id : other.id)?.add(rule.otherFirst ? other.id : step.id);
      }
    }
  }
  const placed: StepDraft[] = [];
  let left = [...steps];
  while (left.length > 0) {
    const next =
      left.find((step) =>
        [...(before.get(step.id) ?? [])].every((id) => placed.some((p) => p.id === id)),
      ) ?? left[0];
    if (next === undefined) {
      break;
    }
    placed.push(next);
    left = left.filter((step) => step !== next);
  }
  return placed;
}
