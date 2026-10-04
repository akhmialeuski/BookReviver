import type {
  PageKind,
  PageSchema,
  RecipeRuleSchema,
  RecipeSchema,
  RuleCondition,
  StageSummarySchema,
} from '@/api';
import type { StripItem, VariantMark, VariantOption } from '@/features/workspace/strip';

/**
 * The variants of a stage as the pages show them: the colour and the name of the variant a page was processed by, the
 * rules that send pages to a variant, and the kinds of page a rule can name.
 *
 * A variant keeps its colour for as long as the stage has it. The colours go by the order the variants were made in and
 * not by the order of the list, which puts the active one first, so activating another variant repaints nothing.
 */

/** The colours of the variants, as the classes that paint a dot. They wrap around for a stage with more variants. */
export const VARIANT_TONES = [
  'bg-variant-1',
  'bg-variant-2',
  'bg-variant-3',
  'bg-variant-4',
  'bg-variant-5',
  'bg-variant-6',
] as const;

/** The conditions on the kind of a page, by the kinds each matches. */
const CONDITION_OF_KIND: Readonly<Partial<Record<PageKind, RuleCondition>>> = {
  plate: 'plates',
  frontispiece: 'plates',
  cover: 'covers',
  'back-cover': 'covers',
  blank: 'blanks',
};

/** List the variants by the order they were made in, the oldest first. */
function madeOrder(recipes: readonly RecipeSchema[]): RecipeSchema[] {
  return [...recipes].sort(
    (a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id),
  );
}

/** Give the class of the colour of a variant, or the first colour for a recipe the stage does not have. */
export function toneOf(recipes: readonly RecipeSchema[], recipeId: string): string {
  const index = madeOrder(recipes).findIndex((recipe) => recipe.id === recipeId);
  return VARIANT_TONES[Math.max(index, 0) % VARIANT_TONES.length] ?? VARIANT_TONES[0];
}

/**
 * Give what the strip shows of the variant of a page.
 *
 * @param recipes Every recipe of the stage.
 * @param item The page with its row in the stage.
 * @returns The mark, or null for a page no recipe processed, and for a stage with a single recipe, where every page would
 * carry the same mark and the mark would say nothing.
 */
export function markOf(recipes: readonly RecipeSchema[], item: StripItem): VariantMark | null {
  const recipeId = item.row?.recipe_id ?? null;
  if (recipes.length < 2 || recipeId === null) {
    return null;
  }
  const recipe = recipes.find((entry) => entry.id === recipeId);
  if (recipe === undefined) {
    return null;
  }
  return {
    name: recipe.name,
    tone: toneOf(recipes, recipe.id),
    pinned: item.row?.pinned === true,
  };
}

/**
 * List the variants the strip can be narrowed to, with the pages each processed, in the order they were made.
 *
 * @returns The options, or none for a stage with a single recipe, which has nothing to choose between.
 */
export function optionsOf(
  recipes: readonly RecipeSchema[],
  items: readonly StripItem[],
): VariantOption[] {
  if (recipes.length < 2) {
    return [];
  }
  return madeOrder(recipes).map((recipe) => ({
    id: recipe.id,
    name: recipe.name,
    pages: items.filter((item) => item.row?.recipe_id === recipe.id).length,
  }));
}

/** Keep the pages a variant processed, or every page for no variant. */
export function applyVariant(
  items: readonly StripItem[],
  variantId: string | null,
): readonly StripItem[] {
  return variantId === null ? items : items.filter((item) => item.row?.recipe_id === variantId);
}

/** Give the condition that names every page of a kind, or null for a kind no rule can name. */
export function conditionOfKind(kind: PageKind): RuleCondition | null {
  return CONDITION_OF_KIND[kind] ?? null;
}

/**
 * Give the condition of the rule that names a page: the plates for a page that shows a picture, whatever its kind, and
 * else the group of its kind, which is none for a plate the reader made text.
 */
export function conditionOfPage(
  page: Pick<PageSchema, 'kind' | 'content_type'>,
): RuleCondition | null {
  if (page.content_type !== 'text') {
    return 'plates';
  }
  const ofKind = conditionOfKind(page.kind);
  return ofKind === 'plates' ? null : ofKind;
}

/** Give the pages the rule that names a page would send to its variant: every plate and every picture for a plate. */
export function pagesLikePage(
  items: readonly StripItem[],
  page: Pick<PageSchema, 'kind' | 'content_type'>,
): StripItem[] {
  const condition = conditionOfPage(page);
  return condition === null
    ? []
    : items.filter(
        (item) => conditionOfPage(item.page) === condition && item.page.origin !== 'placeholder',
      );
}

/** List the rules that send pages to a recipe, in the order they are tried. */
export function rulesOf(
  rules: readonly RecipeRuleSchema[],
  recipeId: string,
): readonly RecipeRuleSchema[] {
  return rules.filter((rule) => rule.recipe_id === recipeId);
}

/** Find the rule of a condition, and of a group for the condition on a group, or undefined when the stage has none. */
export function ruleFor(
  rules: readonly RecipeRuleSchema[],
  condition: RuleCondition,
  groupLabel = '',
): RecipeRuleSchema | undefined {
  return rules.find((rule) => rule.condition === condition && rule.group_label === groupLabel);
}

/**
 * Write how many pages each variant processed, the one with the most pages first, as the parts of a line.
 *
 * @param summary The summary of the stage, or undefined while it is read.
 * @param recipes Every recipe of the stage.
 * @param describe Writes one part from the name of a variant and its pages.
 */
export function countsOf(
  summary: StageSummarySchema | undefined,
  recipes: readonly RecipeSchema[],
  describe: (name: string, pages: number) => string,
): string[] {
  return (summary?.variants ?? []).flatMap(({ recipe_id: recipeId, pages }) => {
    const recipe = recipes.find((entry) => entry.id === recipeId);
    return recipe === undefined ? [] : [describe(recipe.name, pages)];
  });
}
