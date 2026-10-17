import type { StagePageSchema } from '@/api';
import { draftIdAt } from '@/features/processing/recipe';
import { StepList, type StepProgress } from '@/features/processing/StepList';
import { passedPages } from '@/features/processing/stepRuns';
import type { Processing } from '@/features/processing/useProcessing';
import type { StageRun } from '@/features/processing/useStageRun';
import { StepCatalogue } from '@/features/workspace/StepCatalogue';
import { MESSAGES } from '@/shared/messages';

/**
 * The content of the `recipe` slot of the panel on a stage without a step bar: the steps of the recipe as cards, with the
 * same button that adds one as the bar has. The panel passes the slot only for such a stage, and the layout draws the
 * section and its heading.
 *
 * A stage with a step bar keeps its steps there: they are added from the catalogue of the bar, ordered and switched in
 * the window of the gear, and set in the settings frame of the panel for the open step. The settings of the open card of a
 * stage without a bar stand in the settings frame like those of a step of the bar.
 *
 * The profile, the choice of the recipe and the bar that saves it are not here: `RecipeBar` draws them above the panel.
 */

export function RecipeSection({
  processing,
  rows,
  run,
}: {
  processing: Processing;
  rows: readonly StagePageSchema[];
  /** The run of the stage, which tells how many pages the steps can be passed by. Absent for steps that only edit. */
  run?: StageRun;
}): React.JSX.Element | null {
  const { recipe } = processing;
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
  );
}
