import { BookmarkPlusIcon, SettingsIcon } from 'lucide-react';
import { useState } from 'react';
import type { StagePageSchema } from '@/api';
import { RecipeSaveBar } from '@/features/processing/RecipeSaveBar';
import { StepList } from '@/features/processing/StepList';
import type { Processing } from '@/features/processing/useProcessing';
import { SaveProfileDialog } from '@/features/profiles/SaveProfileDialog';
import { ResetSteps } from '@/features/workspace/ResetSteps';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/shared/ui/dialog';

/**
 * The window of the gear in the bar: the list of the steps of the recipe, which is the one the panel of a stage without a
 * bar draws, with the button that keeps the steps as a profile of the account and the one that puts the default steps
 * back. Profiles are applied elsewhere, so the window has no button for that. The settings of a step are not drawn in
 * the list here, since the panel of the open step has them.
 *
 * It is the one place the order, the switches and the removal of the steps of a stage with a bar are set. It edits the
 * draft that the panel of the open step and the save bar read, so a change made here is the change made there.
 * Nothing is written until the bar is pressed.
 */

const labels = MESSAGES.workspace.steps.gear;

export function StepsWindow({
  processing,
  rows,
}: {
  processing: Processing;
  /** The rows of the stage, which the number of pages a save makes out of date is counted from. */
  rows: readonly StagePageSchema[];
}): React.JSX.Element | null {
  const { stage, steps } = processing;
  const [savedAs, setSavedAs] = useState<string | null>(null);
  const [saveOpen, setSaveOpen] = useState(false);
  if (processing.recipe === undefined) {
    return null;
  }

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          className="shrink-0"
          aria-label={labels.open}
          title={labels.open}
          data-testid="steps-gear"
        >
          <SettingsIcon />
        </Button>
      </DialogTrigger>
      <DialogContent
        className="max-h-[85vh] overflow-y-auto sm:max-w-2xl"
        data-testid="steps-window"
      >
        <DialogHeader>
          <DialogTitle>{labels.title(MESSAGES.stages.names[stage])}</DialogTitle>
          <DialogDescription>{labels.hint}</DialogDescription>
        </DialogHeader>
        <StepList processing={processing} />
        <RecipeSaveBar processing={processing} rows={rows} />
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            size="sm"
            title={MESSAGES.profiles.save.hint}
            disabled={!processing.valid || steps.length === 0}
            data-testid="profile-save-open"
            onClick={() => setSaveOpen(true)}
          >
            <BookmarkPlusIcon />
            {MESSAGES.profiles.save.open}
          </Button>
          <ResetSteps processing={processing} rows={rows} />
        </div>
        <SaveProfileDialog
          processing={processing}
          open={saveOpen}
          onOpenChange={setSaveOpen}
          onSaved={setSavedAs}
        />
        {savedAs === null ? null : (
          <p className="text-xs text-muted-foreground" data-testid="profile-saved">
            {MESSAGES.profiles.save.saved(savedAs)}
          </p>
        )}
      </DialogContent>
    </Dialog>
  );
}
