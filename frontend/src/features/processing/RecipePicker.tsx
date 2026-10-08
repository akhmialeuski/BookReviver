import type { Processing } from '@/features/processing/useProcessing';
import { useStageSummaries } from '@/features/workspace/queries';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';

/**
 * The choice of the recipe of a stage that the panel and the step bar show, which is the one place a recipe is chosen.
 *
 * A stage has one recipe for each kind of page, and a page is processed by the recipe of its kind. Each option names the
 * kind and says how many pages of the book are of it, which the summary of the stage gives. A stage with one recipe has
 * nothing to choose, so the picker draws nothing. The caller gives the layout, which differs between the row of the bar
 * and the column of the panel.
 */

const labels = MESSAGES.processing.recipe;

export function RecipePicker({
  processing,
  className,
}: {
  processing: Processing;
  className?: string;
}): React.JSX.Element | null {
  const { recipe, recipes } = processing;
  const summaries = useStageSummaries(processing.projectId);
  if (recipes.length < 2) {
    return null;
  }
  const counted = summaries.data?.find((entry) => entry.stage === processing.stage)?.recipes ?? [];
  return (
    <select
      aria-label={labels.choose}
      data-testid="recipe-select"
      className={cn(
        'min-w-0 rounded-md border border-input bg-background text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50',
        className,
      )}
      value={recipe?.id ?? ''}
      onChange={(event) => processing.chooseRecipe(event.target.value)}
    >
      {recipes.map((entry) => (
        <option key={entry.id} value={entry.id}>
          {labels.option(
            labels.kinds[entry.kind],
            counted.find((one) => one.recipe_id === entry.id)?.pages ?? 0,
          )}
        </option>
      ))}
    </select>
  );
}
