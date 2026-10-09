import type { StagePageSchema } from '@/api';
import { RecipePicker } from '@/features/processing/RecipePicker';
import { RecipeSaveBar } from '@/features/processing/RecipeSaveBar';
import { draftIdAt } from '@/features/processing/recipe';
import { StepList, type StepProgress } from '@/features/processing/StepList';
import { passedPages } from '@/features/processing/stepRuns';
import type { Processing } from '@/features/processing/useProcessing';
import type { StageRun } from '@/features/processing/useStageRun';
import { ProfileMenu } from '@/features/profiles/ProfileMenu';
import { StepCatalogue } from '@/features/workspace/StepCatalogue';
import { hasStepBar } from '@/features/workspace/steps';
import { MESSAGES } from '@/shared/messages';

/**
 * The content of the `recipe` slot of the panel: which recipe is shown, its steps on a stage without a step bar, and the
 * bar that saves what was changed. The layout draws the section and its heading.
 *
 * A stage with a step bar keeps its steps there: they are added from the catalogue of the bar, ordered and switched in
 * the window of the gear, and set in the settings frame of the panel for the open step. The recipe of such a stage lists no
 * steps here. A stage without a bar lists its steps as cards, with the same button that adds one as the bar has, and the
 * settings of the open card stand in the settings frame like those of a step of the bar.
 *
 * Nothing is saved while a step is edited. The draft lives in the state of the screen, so a preview can use it, and the
 * recipe is written only by the button, which says first how many pages the save makes out of date.
 */

export function RecipeSection({
  processing,
  rows,
  run,
  onManageProfiles,
}: {
  processing: Processing;
  rows: readonly StagePageSchema[];
  /** The run of the stage, which tells how many pages the steps can be passed by. Absent for steps that only edit. */
  run?: StageRun;
  /** Opens the library of profiles, which the profile menu offers when it is given. */
  onManageProfiles?: () => void;
}): React.JSX.Element | null {
  const { stage, recipe } = processing;
  if (recipe === undefined) {
    return null;
  }
  const progress: StepProgress | undefined =
    run === undefined
      ? undefined
      : {
          passed: (index) => passedPages(rows, recipe.id, index),
          total: run.total,
        };

  return (
    <>
      <ProfileMenu processing={processing} onManage={onManageProfiles} />
      {hasStepBar(stage) ? null : (
        <>
          <RecipePicker processing={processing} className="h-9 w-full px-3" />
          <StepList
            processing={processing}
            heading={MESSAGES.processing.steps.title}
            progress={progress}
          />
          <StepCatalogue
            processing={processing}
            rows={rows}
            onAdded={(_stepId, index) => processing.open(draftIdAt(index))}
          />
        </>
      )}
      <RecipeSaveBar processing={processing} rows={rows} />
    </>
  );
}
