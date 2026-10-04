import { BookmarkIcon, ChevronDownIcon } from 'lucide-react';
import { useState } from 'react';
import { bodyOf, draftOf } from '@/features/processing/recipe';
import type { Processing } from '@/features/processing/useProcessing';
import { type ProfileChange, profileChanges } from '@/features/profiles/changes';
import { useProfiles, useReplaceProfile } from '@/features/profiles/queries';
import { SaveProfileDialog } from '@/features/profiles/SaveProfileDialog';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { ErrorAlert } from '@/shared/ui/error-alert';
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/ui/popover';

/**
 * The button of the profile the recipe of the stage was made from, with the menu that keeps the steps of the book in it.
 *
 * The button names the profile, and carries a mark as soon as the steps on the screen differ from it. The menu lists the
 * differences and offers three actions: save the steps to the profile, save them as a new profile, and put the steps of
 * the profile back. The two ways of saving are offered only when the steps on the screen are the saved recipe, since a
 * step the reader added has no identifier until the recipe is saved, and a profile saved from it would not match the
 * recipe afterwards. Putting the profile back changes the draft only, and the recipe changes when it is saved.
 */

const labels = MESSAGES.profiles.link;

/** Write one difference as the sentence the menu lists. */
function sentenceOf(change: ProfileChange): string {
  const conditions = MESSAGES.processing.steps.condition.options;
  switch (change.kind) {
    case 'added':
      return labels.changes.added(change.title);
    case 'removed':
      return labels.changes.removed(change.title);
    case 'order':
      return labels.changes.order;
    case 'switched':
      return change.enabled
        ? labels.changes.switchedOn(change.title)
        : labels.changes.switchedOff(change.title);
    case 'condition':
      return labels.changes.condition(change.title, conditions[change.appliesTo].toLowerCase());
    case 'params':
      return labels.changes.params(change.title, change.fields);
  }
}

export function ProfileMenu({ processing }: { processing: Processing }): React.JSX.Element | null {
  const { stage, recipe, steps, catalogue } = processing;
  const [open, setOpen] = useState(false);
  const [saveOpen, setSaveOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const profiles = useProfiles(stage);
  const replace = useReplaceProfile();
  if (recipe === undefined) {
    return null;
  }

  const linked =
    recipe.profile_id === null
      ? undefined
      : profiles.data?.find((profile) => profile.id === recipe.profile_id);
  const changes = linked === undefined ? [] : profileChanges(linked.steps, steps, catalogue);
  const modeDiffers = linked !== undefined && linked.order !== processing.orderMode;
  const canKeep = !processing.dirty && processing.valid;

  return (
    <div className="grid gap-1">
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            size="sm"
            className="w-full min-w-0 justify-between"
            title={linked === undefined ? labels.noneHint : undefined}
            data-testid="profile-button"
          >
            <span className="flex min-w-0 items-center gap-2">
              <BookmarkIcon />
              <span className="shrink-0 text-muted-foreground">{labels.label}</span>
              <span className="truncate font-semibold" data-testid="profile-name">
                {linked?.name ?? labels.none}
              </span>
              {changes.length === 0 ? null : (
                <span
                  className="flex shrink-0 items-center gap-1 text-status-attention"
                  title={labels.changedHint}
                  data-testid="profile-changed"
                >
                  <span className="size-2 rounded-full bg-status-attention" aria-hidden="true" />
                  {labels.changed}
                </span>
              )}
            </span>
            <ChevronDownIcon />
          </Button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-80" data-testid="profile-menu">
          <div className="grid gap-3 p-3">
            <p className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
              {labels.menuTitle(MESSAGES.stages.names[stage])}
            </p>
            {linked === undefined ? (
              <p className="text-sm text-muted-foreground" data-testid="profile-not-linked">
                {labels.notLinked}
              </p>
            ) : (
              <div className="grid gap-1">
                <p className="text-sm font-medium">{linked.name}</p>
                {changes.length === 0 ? (
                  <p className="text-xs text-muted-foreground">{labels.unchanged}</p>
                ) : (
                  <ul
                    aria-label={labels.changes.label}
                    className="grid gap-0.5 text-xs text-muted-foreground"
                    data-testid="profile-changes"
                  >
                    {changes.map((change) => (
                      <li key={sentenceOf(change)}>{sentenceOf(change)}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}
            {canKeep ? null : (
              <p className="text-xs text-status-attention" data-testid="profile-save-first">
                {labels.saveFirst}
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                size="sm"
                title={labels.saveHint}
                disabled={
                  linked === undefined ||
                  !canKeep ||
                  (changes.length === 0 && !modeDiffers) ||
                  replace.isPending
                }
                data-testid="profile-save"
                onClick={() =>
                  linked === undefined
                    ? undefined
                    : replace.mutate(
                        {
                          path: { profile_id: linked.id },
                          body: {
                            name: linked.name,
                            steps: bodyOf(steps),
                            order: processing.orderMode,
                          },
                        },
                        {
                          onSuccess: (saved) => {
                            setNotice(labels.saved(saved.name));
                            setOpen(false);
                          },
                        },
                      )
                }
              >
                {replace.isPending ? labels.saving : labels.save}
              </Button>
              <Button
                variant="outline"
                size="sm"
                title={labels.saveAsNewHint}
                disabled={!canKeep || steps.length === 0}
                data-testid="profile-save-new"
                onClick={() => {
                  setOpen(false);
                  setSaveOpen(true);
                }}
              >
                {labels.saveAsNew}
              </Button>
              <Button
                variant="outline"
                size="sm"
                title={labels.revertHint}
                disabled={linked === undefined || (changes.length === 0 && !modeDiffers)}
                data-testid="profile-revert"
                onClick={() => {
                  if (linked !== undefined) {
                    processing.loadSteps(draftOf(linked), linked.order);
                    setOpen(false);
                  }
                }}
              >
                {labels.revert}
              </Button>
            </div>
            {replace.isError ? <ErrorAlert message={describeError(replace.error)} /> : null}
          </div>
        </PopoverContent>
      </Popover>
      <SaveProfileDialog
        processing={processing}
        open={saveOpen}
        onOpenChange={setSaveOpen}
        onSaved={(name) => setNotice(MESSAGES.profiles.save.saved(name))}
      />
      {notice === null ? null : (
        <p className="text-xs text-muted-foreground" data-testid="profile-saved">
          {notice}
        </p>
      )}
    </div>
  );
}
