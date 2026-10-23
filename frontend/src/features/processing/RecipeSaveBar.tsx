import type { StagePageSchema } from '@/api';
import { useSaveRecipe } from '@/features/processing/queries';
import { bodyOf, pagesToGoStale } from '@/features/processing/recipe';
import type { Processing } from '@/features/processing/useProcessing';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The narrow amber row under the recipe bar that saves the draft of the steps, which `RecipeBar` draws only while the draft
 * differs from the saved recipe.
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
  const { bookId, stage, recipe, steps } = processing;
  const save = useSaveRecipe(bookId);
  if (recipe === undefined || !processing.dirty) {
    return null;
  }
  const stale = pagesToGoStale(rows, recipe.id);

  return (
    <div
      className="grid gap-1 rounded-md border border-status-attention/40 bg-status-attention/10 px-3 py-1.5"
      data-testid="recipe-save-bar"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <p className="flex min-w-0 flex-1 basis-40 items-center gap-2 text-sm">
          <span className="size-2 shrink-0 rounded-full bg-status-attention" aria-hidden="true" />
          <span className="min-w-0">
            {labels.save.unsaved}
            {stale > 0 ? (
              <>
                {' '}
                <span data-testid="recipe-stale-warning">{labels.save.staleWarning(stale)}</span>
              </>
            ) : null}
          </span>
        </p>
        <div className="ml-auto flex gap-2">
          <Button
            size="sm"
            title={labels.save.saveHint}
            disabled={!processing.valid || processing.refused.length > 0 || save.isPending}
            data-testid="recipe-save"
            onClick={() =>
              save.mutate({
                path: { book_id: bookId, stage, recipe_id: recipe.id },
                body: { steps: bodyOf(steps), order: processing.orderMode },
              })
            }
          >
            {save.isPending ? labels.save.saving : labels.save.save}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            title={labels.save.discardHint}
            data-testid="recipe-discard"
            onClick={processing.discard}
          >
            {labels.save.discard}
          </Button>
        </div>
      </div>
      {processing.valid ? null : (
        <p className="text-sm text-destructive">{labels.steps.outOfLimits}</p>
      )}
      {processing.refused.length === 0 ? null : (
        <p className="text-sm text-destructive" data-testid="recipe-order-blocked">
          {labels.steps.order.blocked}
        </p>
      )}
      {save.error === null ? null : <ErrorAlert message={describeError(save.error)} />}
    </div>
  );
}
