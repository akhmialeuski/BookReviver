import { useState } from 'react';
import type { RecipeProfileSchema } from '@/api';
import { useDeleteProfile, useRenameProfile } from '@/features/profiles/queries';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import {
  Dialog,
  DialogClose,
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
 * The two dialogs of a profile in the list of the account: the one that gives it another name, and the one that asks
 * before it is deleted. The button that opens each is the child, so the row decides how it is offered.
 */

const labels = MESSAGES.profiles.page;
const NAME_MAX_LENGTH = 500;

export function RenameProfileDialog({
  profile,
  children,
}: {
  profile: RecipeProfileSchema;
  /** The button that opens the dialog. */
  children: React.ReactNode;
}): React.JSX.Element {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(profile.name);
  const rename = useRenameProfile();

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) {
          setName(profile.name);
          rename.reset();
        }
        setOpen(next);
      }}
    >
      <DialogTrigger asChild>{children}</DialogTrigger>
      <DialogContent>
        <form
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            rename.mutate(
              { path: { profile_id: profile.id }, body: { name: name.trim() } },
              { onSuccess: () => setOpen(false) },
            );
          }}
        >
          <DialogHeader>
            <DialogTitle>{labels.renameTitle}</DialogTitle>
          </DialogHeader>
          <TextField
            label={labels.renameLabel}
            name="profile-name"
            required
            maxLength={NAME_MAX_LENGTH}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
          {rename.isError ? <ErrorAlert message={describeError(rename.error)} /> : null}
          <DialogFooter>
            <Button
              type="submit"
              disabled={rename.isPending || name.trim() === ''}
              data-testid="profile-rename-submit"
            >
              {rename.isPending ? labels.renaming : labels.renameSubmit}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function DeleteProfileDialog({
  profile,
  children,
}: {
  profile: RecipeProfileSchema;
  /** The button that opens the dialog. */
  children: React.ReactNode;
}): React.JSX.Element {
  const [open, setOpen] = useState(false);
  const remove = useDeleteProfile();

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) {
          remove.reset();
        }
        setOpen(next);
      }}
    >
      <DialogTrigger asChild>{children}</DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{labels.removeTitle}</DialogTitle>
          <DialogDescription>{labels.removeDescription(profile.name)}</DialogDescription>
        </DialogHeader>
        {remove.isError ? <ErrorAlert message={describeError(remove.error)} /> : null}
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">{MESSAGES.common.cancel}</Button>
          </DialogClose>
          <Button
            variant="destructive"
            disabled={remove.isPending}
            data-testid="profile-delete-submit"
            onClick={() =>
              remove.mutate(
                { path: { profile_id: profile.id } },
                { onSuccess: () => setOpen(false) },
              )
            }
          >
            {remove.isPending ? labels.removing : labels.removeSubmit}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
