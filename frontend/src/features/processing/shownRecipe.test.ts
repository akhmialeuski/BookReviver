import { describe, expect, it } from 'vitest';
import { recipe, step } from '@/features/processing/fixtures';
import { type RecipeChoice, shownRecipe } from '@/features/processing/useProcessing';

/**
 * The recipe the panel shows: the recipe of the open page's kind, so an edit of a step lands in the recipe that processes
 * the page, unless the reader chose another one on a page of the same kind.
 */

const TEXT = recipe('text', {
  kind: 'text',
  steps: [step('geometry.margins', { step_id: 't-margins' })],
});
const COLOUR = recipe('colour', {
  kind: 'color-picture',
  steps: [step('geometry.margins', { step_id: 'c-margins' })],
});
const RECIPES = [TEXT, COLOUR];

const chose = (id: string, kind: RecipeChoice['kind'], stepId?: string): RecipeChoice => ({
  stage: 'geometry',
  id,
  stepId,
  kind,
});

describe('shownRecipe', () => {
  it('shows the recipe of a colour picture on a colour picture, and of text on text', () => {
    expect(shownRecipe(RECIPES, 'geometry', undefined, 'color-picture', null)?.id).toBe('colour');
    expect(shownRecipe(RECIPES, 'geometry', undefined, 'text', null)?.id).toBe('text');
  });

  it('shows the recipe of the page even when the address names a step of another recipe', () => {
    expect(shownRecipe(RECIPES, 'geometry', 't-margins', 'color-picture', null)?.id).toBe('colour');
  });

  it('keeps the recipe chosen by hand on pages of the same kind, and while its own steps are opened', () => {
    const choice = chose('text', 'color-picture');
    expect(shownRecipe(RECIPES, 'geometry', undefined, 'color-picture', choice)?.id).toBe('text');
    expect(shownRecipe(RECIPES, 'geometry', 't-margins', 'color-picture', choice)?.id).toBe('text');
  });

  it('drops the recipe chosen by hand once a page of another kind is opened', () => {
    const choice = chose('colour', 'text');
    expect(shownRecipe(RECIPES, 'geometry', undefined, 'text', choice)?.id).toBe('colour');
    expect(shownRecipe(RECIPES, 'geometry', undefined, 'bw-picture', choice)?.id).toBe('text');
  });

  it('shows the recipe owning the step of the address while the kind of the page is not known', () => {
    expect(shownRecipe(RECIPES, 'geometry', 'c-margins', undefined, null)?.id).toBe('colour');
    expect(shownRecipe(RECIPES, 'geometry', undefined, undefined, null)?.id).toBe('text');
  });

  it('shows the first recipe for a kind the stage has no recipe of', () => {
    expect(shownRecipe(RECIPES, 'geometry', undefined, 'blank', null)?.id).toBe('text');
  });
});
