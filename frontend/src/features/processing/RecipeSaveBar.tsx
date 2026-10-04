import type { StagePageSchema } from '@/api';
import { useSaveRecipe } from '@/features/processing/queries';
import { bodyOf, pagesToGoStale } from '@/features/processing/recipe';
import type { Processing } from '@/features/processing/useProcessing';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The bar that saves the draft of the steps, which the recipe section of the panel and the window of the gear both show
 * while the draft differs from the saved recipe.
 *
 * It says first how many pages the save makes out of date, and refuses to save a draft whose values do not fit or whose
 * order the usual order refuses. Nothing else writes the recipe.
 */

const labels = MESSAGES.processing;

export function RecipeSaveBar({
  processing,
  rows,
}: {
  processing: Processing;
  rows: readonly StagePageSchema[];
}): React.JSX.Element | null {
  const { projectId, stage, recipe, steps } = processing;
  const save = useSaveRecipe(projectId, stage);
  if (recipe === undefined || !processing.dirty) {
    return null;
  }
  const stale = pagesToGoStale(rows, recipe.id);

  return (
    <div className="grid gap-2 rounded-lg border bg-muted/40 p-3" data-testid="recipe-save-bar">
      <p className="text-sm">{labels.save.unsaved}</p>
      {stale > 0 ? (
        <p className="text-sm text-status-attention" data-testid="recipe-stale-warning">
          {labels.save.staleWarning(stale)}
        </p>
      ) : null}
      {processing.valid ? null : (
        <p className="text-sm text-destructive">{labels.steps.outOfLimits}</p>
      )}
      {processing.refused.length === 0 ? null : (
        <p className="text-sm text-destructive" data-testid="recipe-order-blocked">
          {labels.steps.order.blocked}
        </p>
      )}
      <div className="flex gap-2">
        <Button
          size="sm"
          disabled={!processing.valid || processing.refused.length > 0 || save.isPending}
          data-testid="recipe-save"
          onClick={() =>
            save.mutate({
              path: { project_id: projectId, stage, recipe_id: recipe.id },
              body: { name: recipe.name, steps: bodyOf(steps), order: processing.orderMode },
            })
          }
        >
          {save.isPending ? labels.save.saving : labels.save.save}
        </Button>
        <Button variant="ghost" size="sm" onClick={processing.discard}>
          {labels.save.discard}
        </Button>
      </div>
      {save.error === null ? null : <ErrorAlert message={describeError(save.error)} />}
    </div>
  );
}
