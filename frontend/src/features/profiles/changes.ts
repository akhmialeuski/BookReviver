import type { AppliesTo, ProcessorSchema, StepSchema } from '@/api';
import { type StepDraft, sameValue } from '@/features/processing/recipe';
import { formSchemaOf, methodsOf } from '@/features/processing/schema';

/**
 * How the steps of a book differ from the steps of the profile it was made from.
 *
 * Steps are matched by the identifier the server gave them, which a profile keeps when it is applied to a book and when
 * a book is saved to it, so a step that was moved or changed is the same step on both sides. A step the draft added has
 * no identifier yet and counts as added. The order of the steps is reported once, for the steps both sides have, since
 * moving one step past three others would otherwise be reported as four moves.
 */

/** One difference between the draft and the profile. */
export type ProfileChange =
  | { kind: 'added'; title: string }
  | { kind: 'removed'; title: string }
  | { kind: 'order' }
  | { kind: 'switched'; title: string; enabled: boolean }
  | { kind: 'condition'; title: string; appliesTo: AppliesTo }
  | { kind: 'params'; title: string; fields: readonly string[] };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Give the title of a field of a processor, which is the label its form shows, or the name when it has none. */
function fieldTitle(processor: ProcessorSchema | undefined, name: string): string {
  const whole = formSchemaOf(processor?.parameters ?? {});
  const schemas = [whole, ...methodsOf(whole)];
  for (const schema of schemas) {
    const properties = isRecord(schema.properties) ? schema.properties : {};
    const property = properties[name];
    if (isRecord(property) && typeof property.title === 'string') {
      return property.title;
    }
  }
  return name;
}

/** Give the names of the parameters whose values differ, in the order the saved step has them. */
function changedFields(saved: Record<string, unknown>, drafted: Record<string, unknown>): string[] {
  const names = new Set([...Object.keys(saved), ...Object.keys(drafted)]);
  return [...names].filter((name) => !sameValue(saved[name], drafted[name]));
}

/**
 * List what the draft changed in the steps of a profile.
 *
 * @param profile The steps of the profile, in their order.
 * @param draft The steps of the book as they are on the screen, in their order.
 * @param catalogue The processors of the stage, whose titles name the steps.
 * @returns The differences, in the order of the draft, with the removed steps last. Empty when there are none.
 */
export function profileChanges(
  profile: readonly StepSchema[],
  draft: readonly StepDraft[],
  catalogue: readonly ProcessorSchema[],
): ProfileChange[] {
  const processorOf = (key: string): ProcessorSchema | undefined =>
    catalogue.find((entry) => entry.key === key);
  const titleOf = (key: string): string => processorOf(key)?.title ?? key;
  const saved = new Map(profile.map((step) => [step.step_id, step]));
  const kept = new Set(draft.map((step) => step.stepId));
  const found: ProfileChange[] = [];

  for (const step of draft) {
    const before = step.stepId === null ? undefined : saved.get(step.stepId);
    if (before === undefined) {
      found.push({ kind: 'added', title: titleOf(step.processorKey) });
      continue;
    }
    const title = titleOf(step.processorKey);
    if (before.enabled !== step.enabled) {
      found.push({ kind: 'switched', title, enabled: step.enabled });
    }
    if (before.applies_to !== step.appliesTo) {
      found.push({ kind: 'condition', title, appliesTo: step.appliesTo });
    }
    const fields = changedFields(before.params, step.params);
    if (fields.length > 0) {
      const processor = processorOf(step.processorKey);
      found.push({
        kind: 'params',
        title,
        fields: fields.map((name) => fieldTitle(processor, name)),
      });
    }
  }

  const common = (id: string | null): boolean => id !== null && saved.has(id);
  const draftOrder = draft.map((step) => step.stepId).filter(common);
  const profileOrder = profile.map((step) => step.step_id).filter((id) => kept.has(id));
  if (draftOrder.some((id, index) => id !== profileOrder[index])) {
    found.push({ kind: 'order' });
  }

  for (const step of profile) {
    if (!kept.has(step.step_id)) {
      found.push({ kind: 'removed', title: titleOf(step.processor_key) });
    }
  }
  return found;
}
