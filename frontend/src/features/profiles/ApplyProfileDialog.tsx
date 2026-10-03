import { BookmarkCheckIcon } from 'lucide-react';
import { useState } from 'react';
import type { AppliedProfileSchema } from '@/api';
import type { Processing } from '@/features/processing/useProcessing';
import { useApplyProfile, useProfiles } from '@/features/profiles/queries';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { CheckboxField } from '@/shared/ui/checkbox-field';
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
import { SelectField } from '@/shared/ui/select-field';

/**
 * The button that adds a saved profile to the open book as a variant of the stage, and optionally makes it the active
 * recipe.
 *
 * The profiles offered are those of this stage, which the server lists for the signed-in account only. The recipe the
 * profile made is opened in the panel when it is applied.
 */

const labels = MESSAGES.profiles.apply;

export function ApplyProfileDialog({
  processing,
  onApplied,
}: {
  processing: Processing;
  /** Called with the answer of the server, which names the processors the profile needs and the machine lacks. */
  onApplied: (applied: AppliedProfileSchema) => void;
}): React.JSX.Element {
  const { projectId, stage } = processing;
  const [open, setOpen] = useState(false);
  const [chosenId, setChosenId] = useState<string | null>(null);
  const [activate, setActivate] = useState(false);
  const profiles = useProfiles(stage, open);
  const apply = useApplyProfile(projectId, stage);
  const list = profiles.data ?? [];
  const chosen = list.find((profile) => profile.id === chosenId) ?? list[0];

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) {
          setChosenId(null);
          setActivate(false);
          apply.reset();
        }
        setOpen(next);
      }}
    >
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" title={labels.hint} data-testid="profile-apply-open">
          <BookmarkCheckIcon />
          {labels.open}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (chosen === undefined) {
              return;
            }
            apply.mutate(
              { path: { project_id: projectId, profile_id: chosen.id }, body: { activate } },
              {
                onSuccess: (applied) => {
                  processing.chooseRecipe(applied.recipe.id);
                  setOpen(false);
                  onApplied(applied);
                },
              },
            );
          }}
        >
          <DialogHeader>
            <DialogTitle>{labels.title}</DialogTitle>
            <DialogDescription>{labels.description}</DialogDescription>
          </DialogHeader>
          {profiles.isError ? <ErrorAlert message={labels.loadFailed} /> : null}
          {list.length === 0 ? (
            profiles.isPending ? (
              <p className="text-sm text-muted-foreground">{MESSAGES.common.loading}</p>
            ) : (
              <p className="text-sm text-muted-foreground" data-testid="profile-apply-empty">
                {labels.none}
              </p>
            )
          ) : (
            <>
              <SelectField
                label={labels.choose}
                name="profile"
                data-testid="profile-apply-select"
                value={chosen?.id ?? ''}
                onChange={(event) => setChosenId(event.target.value)}
              >
                {list.map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {labels.option(profile.name, profile.is_default, profile.steps.length)}
                  </option>
                ))}
              </SelectField>
              <div className="grid gap-1">
                <CheckboxField
                  label={labels.activate}
                  checked={activate}
                  data-testid="profile-apply-activate"
                  onChange={(event) => setActivate(event.target.checked)}
                />
                <p className="text-xs text-muted-foreground">{labels.activateHint}</p>
              </div>
            </>
          )}
          {apply.isError ? <ErrorAlert message={describeError(apply.error)} /> : null}
          <DialogFooter>
            <Button
              type="submit"
              disabled={chosen === undefined || apply.isPending}
              data-testid="profile-apply-submit"
            >
              {apply.isPending ? labels.submitting : labels.submit}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
