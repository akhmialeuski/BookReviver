import { BookmarkPlusIcon } from 'lucide-react';
import { useState } from 'react';
import { bodyOf } from '@/features/processing/recipe';
import type { Processing } from '@/features/processing/useProcessing';
import { useSaveProfile } from '@/features/profiles/queries';
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
import { TextField } from '@/shared/ui/text-field';

/**
 * The button that saves the steps on the screen as a profile of the account. It asks for a name, which starts as the
 * name of the recipe on the screen.
 *
 * The steps are the draft the reader is looking at, saved or not, so a recipe set up for one book can be kept without
 * saving it to that book first.
 */

const labels = MESSAGES.profiles.save;
const NAME_MAX_LENGTH = 500;

export function SaveProfileDialog({
  processing,
  onSaved,
}: {
  processing: Processing;
  /** Called with the name of the profile once it is stored. */
  onSaved: (name: string) => void;
}): React.JSX.Element | null {
  const { stage, recipe, steps } = processing;
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const save = useSaveProfile();
  if (recipe === undefined) {
    return null;
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) {
          setName(recipe.name);
          save.reset();
        }
        setOpen(next);
      }}
    >
      <DialogTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          title={labels.hint}
          disabled={!processing.valid || steps.length === 0}
          data-testid="profile-save-open"
        >
          <BookmarkPlusIcon />
          {labels.open}
        </Button>
      </DialogTrigger>
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
                onSuccess: (profile) => {
                  setOpen(false);
                  onSaved(profile.name);
                },
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
          {save.isError ? <ErrorAlert message={describeError(save.error)} /> : null}
          <DialogFooter>
            <Button
              type="submit"
              disabled={save.isPending || name.trim() === ''}
              data-testid="profile-save-submit"
            >
              {save.isPending ? labels.submitting : labels.submit}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
