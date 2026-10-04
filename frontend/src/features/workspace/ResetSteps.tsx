import { RotateCcwIcon } from 'lucide-react';
import { useState } from 'react';
import type { StagePageSchema } from '@/api';
import { useResetRecipe } from '@/features/processing/queries';
import { pagesToGoStale } from '@/features/processing/recipe';
import type { Processing } from '@/features/processing/useProcessing';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/shared/ui/dialog';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The button of the gear window that puts the steps a stage starts with back into the recipe, and the confirmation that
 * comes first.
 *
 * Which steps are the default ones is the server's choice: those of the default profile of the account when it has a
 * usable one, and otherwise the built-in template of the recipe. The confirmation says how many pages the reset makes out
 * of date, and that the changes of the draft which are not saved are dropped with it, since the draft is made again from
 * the recipe the server answers with.
 */

const labels = MESSAGES.workspace.steps.gear.reset;

export function ResetSteps({
  processing,
  rows,
}: {
  processing: Processing;
  /** The rows of the stage, which the number of pages the reset makes out of date is counted from. */
  rows: readonly StagePageSchema[];
}): React.JSX.Element | null {
  const { projectId, stage, recipe } = processing;
  const reset = useResetRecipe(projectId, stage);
  const [open, setOpen] = useState(false);
  if (recipe === undefined) {
    return null;
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        reset.reset();
      }}
    >
      <DialogTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="w-fit"
          title={labels.hint}
          data-testid="steps-reset"
        >
          <RotateCcwIcon />
          {labels.open}
        </Button>
      </DialogTrigger>
      <DialogContent data-testid="steps-reset-dialog">
        <DialogHeader>
          <DialogTitle>{labels.title}</DialogTitle>
          <DialogDescription>
            <span data-testid="steps-reset-pages">
              {labels.body(pagesToGoStale(rows, recipe.id))}
            </span>
            {processing.dirty ? (
              <>
                {' '}
                <span data-testid="steps-reset-unsaved">{labels.unsaved}</span>
              </>
            ) : null}
          </DialogDescription>
        </DialogHeader>
        {reset.error === null ? null : <ErrorAlert message={describeError(reset.error)} />}
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            {labels.cancel}
          </Button>
          <Button
            variant="destructive"
            disabled={reset.isPending}
            data-testid="steps-reset-confirm"
            onClick={() =>
              reset.mutate(
                { path: { project_id: projectId, stage, recipe_id: recipe.id } },
                {
                  onSuccess: () => {
                    processing.discard();
                    setOpen(false);
                  },
                },
              )
            }
          >
            {reset.isPending ? labels.working : labels.confirm}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
