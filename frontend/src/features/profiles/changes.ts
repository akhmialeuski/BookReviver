import type { ProcessorSchema, StepSchema } from '@/api';
import { draftIdAt, type StepDraft, sameValue } from '@/features/processing/recipe';
import { formSchemaOf, methodsOf } from '@/features/processing/schema';

/**
 * How the steps of a book differ from the steps of the profile it was made from.
 *
 * Steps are matched by the identifier the server gave them, which a profile keeps when a book is saved to it, or by the
 * step of the profile a step of the book was made from when the profile was applied, so a step that was moved or
 * changed is the same step on both sides. A step the draft added has no identifier yet and counts as added. The order of the steps is reported once, for the steps both sides have, since
 * moving one step past three others would otherwise be reported as four moves.
 */

/** One difference between the draft and the profile. */
export type ProfileChange =
  | { kind: 'added'; title: string }
  | { kind: 'removed'; title: string }
  | { kind: 'order' }
  | { kind: 'switched'; title: string; enabled: boolean }
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
  // The step of the profile a step of the draft is, by its own identifier or by the one it was made from
  const profileIdOf = (step: StepDraft): string | null => {
    const candidates = [step.stepId, step.originStepId ?? null];
    return candidates.find((id): id is string => id !== null && saved.has(id)) ?? null;
  };
  const matched = draft.map(profileIdOf);
  const kept = new Set(matched);
  const found: ProfileChange[] = [];

  for (const [index, step] of draft.entries()) {
    const id = matched[index] ?? null;
    const before = id === null ? undefined : saved.get(id);
    if (before === undefined) {
      found.push({ kind: 'added', title: titleOf(step.processorKey) });
      continue;
    }
    const title = titleOf(step.processorKey);
    if (before.enabled !== step.enabled) {
      found.push({ kind: 'switched', title, enabled: step.enabled });
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

  const draftOrder = matched.filter((id): id is string => id !== null);
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

/**
 * Give the steps of a profile as a draft of the recipe that is put back to it.
 *
 * Each step of the profile takes the identifier of the saved step of the recipe that was made from it, or that the profile
 * was saved from, so the step keeps what the pages hold for it, and a step of the profile the recipe has none of is added
 * as a new step made from it. Each saved step is taken once.
 *
 * @param profile The steps of the profile, in their order.
 * @param saved The saved steps of the recipe.
 * @returns The draft, in the order of the profile.
 */
export function draftFromProfile(
  profile: readonly StepSchema[],
  saved: readonly StepSchema[],
): StepDraft[] {
  const free = [...saved];
  return profile.map((step, index) => {
    const found = free.findIndex(
      (held) => held.step_id === step.step_id || held.origin_step_id === step.step_id,
    );
    const [taken] = found < 0 ? [] : free.splice(found, 1);
    return {
      id: draftIdAt(index),
      stepId: taken?.step_id ?? null,
      originStepId: step.step_id,
      processorKey: step.processor_key,
      params: { ...step.params },
      enabled: step.enabled,
    };
  });
}
