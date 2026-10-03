import { arrayMove } from '@dnd-kit/sortable';
import type { ProcessorSchema, RecipeSchema, StagePageSchema, StepBody } from '@/api';
import { withMarginsSource } from '@/features/processing/margins';
import { defaultsOf, formSchemaOf } from '@/features/processing/schema';

/**
 * The steps of a recipe as the panel edits them: a draft that the reader changes without saving, the operations on it,
 * and the questions the panel asks of it.
 *
 * A saved step has no identity of its own, and a recipe may name a processor twice, so a draft step carries an `id`
 * that the list can be dragged by. The ids are never sent to the server.
 */

/** One step of the recipe being edited. */
export interface StepDraft {
  /** Identity inside the draft, for the list to drag and to key by. */
  id: string;
  processorKey: string;
  params: Record<string, unknown>;
  enabled: boolean;
}

const ID_PREFIX = 'step-';

function idNumber(id: string): number {
  return Number(id.slice(ID_PREFIX.length));
}

/** Start a draft from the saved steps of a recipe. */
export function draftOf(recipe: Pick<RecipeSchema, 'steps'>): StepDraft[] {
  return recipe.steps.map((step, index) => ({
    id: `${ID_PREFIX}${index}`,
    processorKey: step.processor_key,
    params: { ...step.params },
    enabled: step.enabled,
  }));
}

/** Give the steps as the API takes them, in a recipe to save or in a preview. */
export function bodyOf(steps: readonly StepDraft[]): StepBody[] {
  return steps.map((step) => ({
    processor_key: step.processorKey,
    params: step.params,
    enabled: step.enabled,
  }));
}

/** Put the step `activeId` where the step `overId` stands, which is what a drop on it asks for. */
export function moveStep(
  steps: readonly StepDraft[],
  activeId: string,
  overId: string,
): StepDraft[] {
  const from = steps.findIndex((step) => step.id === activeId);
  const to = steps.findIndex((step) => step.id === overId);
  return from < 0 || to < 0 || from === to ? [...steps] : arrayMove([...steps], from, to);
}

/** Switch a step on or off, which keeps its parameters. */
export function toggleStep(steps: readonly StepDraft[], id: string): StepDraft[] {
  return steps.map((step) => (step.id === id ? { ...step, enabled: !step.enabled } : step));
}

/** Take a step out of the draft. */
export function removeStep(steps: readonly StepDraft[], id: string): StepDraft[] {
  return steps.filter((step) => step.id !== id);
}

/**
 * Replace the parameters of a step with what its form holds. A change of a margin of the normalize step also marks its
 * margins as set by hand, so the next measure of the book leaves them.
 */
export function setStepParams(
  steps: readonly StepDraft[],
  id: string,
  params: Record<string, unknown>,
): StepDraft[] {
  return steps.map((step) =>
    step.id === id
      ? { ...step, params: withMarginsSource(step.processorKey, step.params, params) }
      : step,
  );
}

/**
 * Add a step of a processor at the end, with the defaults of its parameter schema.
 *
 * @param steps The draft.
 * @param processor The processor of the catalogue to add.
 */
export function addStep(steps: readonly StepDraft[], processor: ProcessorSchema): StepDraft[] {
  const next = steps.reduce((highest, step) => Math.max(highest, idNumber(step.id)), -1) + 1;
  return [
    ...steps,
    {
      id: `${ID_PREFIX}${next}`,
      processorKey: processor.key,
      params: defaultsOf(formSchemaOf(processor.parameters)),
      enabled: true,
    },
  ];
}

function sameValue(a: unknown, b: unknown): boolean {
  if (Array.isArray(a) || Array.isArray(b)) {
    return (
      Array.isArray(a) &&
      Array.isArray(b) &&
      a.length === b.length &&
      a.every((item, index) => sameValue(item, b[index]))
    );
  }
  if (typeof a === 'object' && a !== null && typeof b === 'object' && b !== null) {
    const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
    return [...keys].every((key) =>
      sameValue((a as Record<string, unknown>)[key], (b as Record<string, unknown>)[key]),
    );
  }
  return a === b;
}

/** Tell whether the draft holds the same steps, in the same order with the same values, as the saved recipe. */
export function sameAsSaved(
  recipe: Pick<RecipeSchema, 'steps'>,
  steps: readonly StepDraft[],
): boolean {
  return sameValue(bodyOf(draftOf(recipe)), bodyOf(steps));
}

/**
 * Count the pages whose result a save of the recipe would make out of date: those it has processed and that are up to
 * date now.
 *
 * @param rows The rows of the stage.
 * @param recipeId The recipe being saved.
 */
export function pagesToGoStale(rows: readonly StagePageSchema[], recipeId: string): number {
  return rows.filter((row) => row.recipe_id === recipeId && row.status === 'fresh').length;
}

/**
 * Tell whether the steps up to one of them can be previewed on a page: a step that cuts a scan into pages cannot,
 * since a preview makes one image of a page, and there must be a step that is on.
 *
 * @param steps The draft.
 * @param catalogue The processors of the stage.
 * @param index The step whose result is wanted.
 */
export function canPreview(
  steps: readonly StepDraft[],
  catalogue: readonly ProcessorSchema[],
  index: number,
): boolean {
  const wanted = steps.slice(0, index + 1).filter((step) => step.enabled);
  return (
    wanted.length > 0 &&
    wanted.every((step) => {
      const processor = catalogue.find((entry) => entry.key === step.processorKey);
      return processor !== undefined && processor.scope === 'page';
    })
  );
}
