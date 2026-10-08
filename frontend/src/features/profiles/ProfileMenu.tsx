import { BookmarkIcon, ChevronDownIcon } from 'lucide-react';
import { useState } from 'react';
import { useSaveRecipe } from '@/features/processing/queries';
import { bodyOf, draftOf } from '@/features/processing/recipe';
import type { Processing } from '@/features/processing/useProcessing';
import { type ProfileChange, profileChanges } from '@/features/profiles/changes';
import { useApplyProfile, useProfiles, useReplaceProfile } from '@/features/profiles/queries';
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
 * the profile back. When the steps on the screen are not yet the saved recipe, the two ways of saving first save the
 * recipe, as the save bar does, and write the profile from the saved recipe, so the steps have the same identifiers on
 * both sides and no false difference appears. Putting the profile back changes the draft only, and the recipe changes
 * when it is saved.
 *
 * Under the three actions stand the other profiles of the stage, each of which is applied to the recipe shown by a
 * press, and the entry that opens the library of profiles.
 */

const labels = MESSAGES.profiles.link;

/** Write one difference as the sentence the menu lists. */
function sentenceOf(change: ProfileChange): string {
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
    case 'params':
      return labels.changes.params(change.title, change.fields);
  }
}

export function ProfileMenu({
  processing,
  onManage,
}: {
  processing: Processing;
  /** Opens the library of profiles, or absent where the library is not available. */
  onManage?: () => void;
}): React.JSX.Element | null {
  const { stage, recipe, steps, catalogue } = processing;
  const [open, setOpen] = useState(false);
  const [saveOpen, setSaveOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const profiles = useProfiles(stage);
  const replace = useReplaceProfile();
  const saveRecipe = useSaveRecipe(processing.projectId, stage);
  const apply = useApplyProfile(processing.projectId, stage);
  if (recipe === undefined) {
    return null;
  }

  const linked =
    recipe.profile_id === null
      ? undefined
      : profiles.data?.find((profile) => profile.id === recipe.profile_id);
  const changes = linked === undefined ? [] : profileChanges(linked.steps, steps, catalogue);
  const error = saveRecipe.error ?? replace.error ?? apply.error;
  const others = (profiles.data ?? []).filter((profile) => profile.id !== linked?.id);
  const modeDiffers = linked !== undefined && linked.order !== processing.orderMode;
  const canKeep = processing.valid && processing.refused.length === 0;

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
            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                size="sm"
                title={labels.saveHint}
                disabled={
                  linked === undefined ||
                  !canKeep ||
                  (changes.length === 0 && !modeDiffers) ||
                  replace.isPending ||
                  saveRecipe.isPending
                }
                data-testid="profile-save"
                onClick={async () => {
                  if (linked === undefined) {
                    return;
                  }
                  try {
                    const kept = processing.dirty
                      ? await saveRecipe.mutateAsync({
                          path: { project_id: processing.projectId, stage, recipe_id: recipe.id },
                          body: { steps: bodyOf(steps), order: processing.orderMode },
                        })
                      : { steps: bodyOf(steps) };
                    const saved = await replace.mutateAsync({
                      path: { profile_id: linked.id },
                      body: { name: linked.name, steps: kept.steps, order: processing.orderMode },
                    });
                    setNotice(labels.saved(saved.name));
                    setOpen(false);
                  } catch {
                    // The error of the failed request is shown under the buttons
                  }
                }}
              >
                {replace.isPending || saveRecipe.isPending ? labels.saving : labels.save}
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
            <div className="grid gap-1 border-t pt-2">
              <p className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                {labels.others}
              </p>
              {others.length === 0 ? (
                <p className="text-xs text-muted-foreground" data-testid="profile-others-empty">
                  {labels.othersEmpty}
                </p>
              ) : (
                <ul className="grid max-h-40 gap-0.5 overflow-y-auto" data-testid="profile-others">
                  {others.map((profile) => (
                    <li key={profile.id}>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="w-full min-w-0 justify-between"
                        title={processing.dirty ? labels.switchBlocked : labels.switchHint}
                        disabled={processing.dirty || apply.isPending}
                        data-testid="profile-switch"
                        data-name={profile.name}
                        onClick={() =>
                          apply.mutate(
                            {
                              path: { project_id: processing.projectId, profile_id: profile.id },
                              body: { kind: recipe.kind },
                            },
                            {
                              onSuccess: (applied) => {
                                processing.chooseRecipe(applied.recipe.id);
                                setNotice(
                                  [
                                    MESSAGES.profiles.library.appliedBook(
                                      MESSAGES.processing.recipe.kinds[applied.recipe.kind],
                                      profile.name,
                                    ),
                                    ...(applied.missing_processors.length === 0
                                      ? []
                                      : [
                                          MESSAGES.profiles.apply.leftOut(
                                            applied.missing_processors,
                                          ),
                                        ]),
                                  ].join(' '),
                                );
                                setOpen(false);
                              },
                            },
                          )
                        }
                      >
                        <span className="truncate">{profile.name}</span>
                        {profile.is_default ? (
                          <span className="shrink-0 text-xs text-muted-foreground">
                            {MESSAGES.profiles.library.defaultMark}
                          </span>
                        ) : null}
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
              {onManage === undefined ? null : (
                <Button
                  variant="ghost"
                  size="sm"
                  className="justify-start"
                  title={labels.manageHint}
                  data-testid="profile-manage"
                  onClick={() => {
                    setOpen(false);
                    onManage();
                  }}
                >
                  {labels.manage}
                </Button>
              )}
            </div>
            {error === null ? null : <ErrorAlert message={describeError(error)} />}
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
