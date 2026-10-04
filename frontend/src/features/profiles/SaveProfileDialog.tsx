import { useEffect, useState } from 'react';
import { useSaveRecipe } from '@/features/processing/queries';
import { bodyOf } from '@/features/processing/recipe';
import type { Processing } from '@/features/processing/useProcessing';
import { useLinkProfile, useSaveProfile } from '@/features/profiles/queries';
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
} from '@/shared/ui/dialog';
import { ErrorAlert } from '@/shared/ui/error-alert';
import { TextField } from '@/shared/ui/text-field';

/**
 * The dialog that saves the steps of the book as a new profile of the account. It asks for a name, which starts as the
 * name of the recipe, and links the recipe to the profile it made, so the book is compared with that profile from then
 * on. The profile menu opens it.
 *
 * The steps are the draft the reader is looking at. When the draft is not yet the saved recipe, the recipe is saved first,
 * as the save bar does, and the profile is written from the saved recipe, so the profile and the recipe hold the same
 * steps with the same identifiers.
 */

const labels = MESSAGES.profiles.save;
const NAME_MAX_LENGTH = 500;

export function SaveProfileDialog({
  processing,
  open,
  onOpenChange,
  onSaved,
}: {
  processing: Processing;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called with the name of the profile once it is stored and the recipe is linked to it. */
  onSaved: (name: string) => void;
}): React.JSX.Element | null {
  const { projectId, stage, recipe, steps } = processing;
  const [name, setName] = useState('');
  const save = useSaveProfile();
  const link = useLinkProfile(projectId, stage);
  const saveRecipe = useSaveRecipe(projectId, stage);
  const recipeName = recipe?.name;
  const { reset: resetSave } = save;
  const { reset: resetLink } = link;
  const { reset: resetSaveRecipe } = saveRecipe;
  // The menu opens the dialog by setting `open`, which Radix does not report as a change, so the name starts here
  useEffect(() => {
    if (open) {
      setName(recipeName ?? '');
      resetSave();
      resetLink();
      resetSaveRecipe();
    }
  }, [open, recipeName, resetSave, resetLink, resetSaveRecipe]);
  if (recipe === undefined) {
    return null;
  }
  const error = saveRecipe.error ?? save.error ?? link.error;
  const busy = saveRecipe.isPending || save.isPending || link.isPending;

  /** Save the recipe when the draft differs from it, then the profile from the saved steps, then the link. */
  async function keep(): Promise<void> {
    if (recipe === undefined) {
      return;
    }
    try {
      const kept = processing.dirty
        ? await saveRecipe.mutateAsync({
            path: { project_id: projectId, stage, recipe_id: recipe.id },
            body: { name: recipe.name, steps: bodyOf(steps), order: processing.orderMode },
          })
        : { steps: bodyOf(steps) };
      const profile = await save.mutateAsync({
        body: { stage, name: name.trim(), steps: kept.steps, order: processing.orderMode },
      });
      await link.mutateAsync({
        path: { project_id: projectId, stage, recipe_id: recipe.id },
        body: { profile_id: profile.id },
      });
      onOpenChange(false);
      onSaved(profile.name);
    } catch {
      // The error of the failed request is shown in the dialog
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <form
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            void keep();
          }}
        >
          <DialogHeader>
            <DialogTitle>{labels.title}</DialogTitle>
            <DialogDescription>{labels.description}</DialogDescription>
          </DialogHeader>
          <TextField
            label={labels.nameLabel}
            name="profile-name"
            required
            maxLength={NAME_MAX_LENGTH}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
          {error === null ? null : <ErrorAlert message={describeError(error)} />}
          <DialogFooter>
            <Button
              type="submit"
              disabled={busy || name.trim() === ''}
              data-testid="profile-save-submit"
            >
              {busy ? labels.submitting : labels.submit}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
