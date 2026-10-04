import { useState } from 'react';
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
 * The steps are the draft the reader is looking at. The menu offers the dialog only when the draft is the saved recipe,
 * so the profile and the recipe hold the same steps with the same identifiers.
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
  if (recipe === undefined) {
    return null;
  }
  const error = save.error ?? link.error;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) {
          setName(recipe.name);
          save.reset();
          link.reset();
        }
        onOpenChange(next);
      }}
    >
      <DialogContent>
        <form
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            save.mutate(
              {
                body: {
                  stage,
                  name: name.trim(),
                  steps: bodyOf(steps),
                  order: processing.orderMode,
                },
              },
              {
                onSuccess: (profile) =>
                  link.mutate(
                    {
                      path: { project_id: projectId, stage, recipe_id: recipe.id },
                      body: { profile_id: profile.id },
                    },
                    {
                      onSuccess: () => {
                        onOpenChange(false);
                        onSaved(profile.name);
                      },
                    },
                  ),
              },
            );
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
              disabled={save.isPending || link.isPending || name.trim() === ''}
              data-testid="profile-save-submit"
            >
              {save.isPending || link.isPending ? labels.submitting : labels.submit}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
