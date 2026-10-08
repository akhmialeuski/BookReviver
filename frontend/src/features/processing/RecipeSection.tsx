import { PlusIcon } from 'lucide-react';
import type { ProcessorSchema, StagePageSchema } from '@/api';
import { isPlacement } from '@/features/editors/placement';
import { MeasureBook } from '@/features/processing/MeasureBook';
import { PageStepSettings } from '@/features/processing/PageStepSettings';
import { pageValuesOf } from '@/features/processing/pageSettings';
import { usePageSettings } from '@/features/processing/queries';
import { RecipePicker } from '@/features/processing/RecipePicker';
import { RecipeSaveBar } from '@/features/processing/RecipeSaveBar';
import type { StepDraft } from '@/features/processing/recipe';
import { StepList, type StepProgress } from '@/features/processing/StepList';
import { StepReset } from '@/features/processing/StepReset';
import { passedPages } from '@/features/processing/stepRuns';
import type { Processing } from '@/features/processing/useProcessing';
import type { StageRun } from '@/features/processing/useStageRun';
import { ProfileMenu } from '@/features/profiles/ProfileMenu';
import { roadmapOf } from '@/features/stages/roadmap';
import { hasStepBar } from '@/features/workspace/steps';
import { MESSAGES } from '@/shared/messages';
import { Badge } from '@/shared/ui/badge';
import { Button } from '@/shared/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu';

/**
 * The recipe of a stage in the panel: which recipe is shown, its steps, and the bar that saves what was changed.
 *
 * A stage with a step bar keeps its steps there: they are added from the catalogue of the bar, ordered and switched in
 * the window of the gear, and set in the panel of the open step. The recipe of such a stage lists no steps here. A stage
 * without a bar lists its steps, with their settings, the steps that can be added and the ones that are coming.
 *
 * Nothing is saved while a step is edited. The draft lives in the state of the screen, so a preview can use it, and the
 * recipe is written only by the button, which says first how many pages the save makes out of date.
 *
 * When a page is open, a step that is open in the list also shows what that page changes for the step, and marks those
 * fields in its form.
 */

const labels = MESSAGES.processing;

export function RecipeSection({
  processing,
  rows,
  run,
  pageId,
  onManageProfiles,
  selected,
}: {
  processing: Processing;
  rows: readonly StagePageSchema[];
  /** The run of the stage, which tells how many pages the steps can be passed by. Absent for steps that only edit. */
  run?: StageRun;
  /** The page that is open, whose own settings of the steps are shown, or absent when none is. */
  pageId?: string;
  /** Opens the library of profiles, which the profile menu offers when it is given. */
  onManageProfiles?: () => void;
  /** The pages selected in the grid, which a setting of the open page can be carried over to. */
  selected?: ReadonlySet<string>;
}): React.JSX.Element | null {
  const { stage, recipe } = processing;
  if (recipe === undefined) {
    return null;
  }

  return (
    <section className="grid grid-cols-1 gap-3" aria-label={labels.recipe.label}>
      <div className="flex min-h-6 items-center justify-between gap-2">
        <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
          {labels.recipe.label}
        </h3>
      </div>
      <ProfileMenu processing={processing} onManage={onManageProfiles} />
      {hasStepBar(stage) ? null : (
        <RecipePicker processing={processing} className="h-9 w-full px-3" />
      )}

      {hasStepBar(stage) ? null : (
        <RecipeSteps
          processing={processing}
          rows={rows}
          run={run}
          pageId={pageId}
          selected={selected}
        />
      )}

      <RecipeSaveBar processing={processing} rows={rows} />
    </section>
  );
}

/** The steps of a stage that has no step bar: the list with its settings, the menu that adds one and the steps to come. */
function RecipeSteps({
  processing,
  rows,
  run,
  pageId,
  selected,
}: {
  processing: Processing;
  rows: readonly StagePageSchema[];
  run?: StageRun;
  pageId?: string;
  selected?: ReadonlySet<string>;
}): React.JSX.Element | null {
  const { projectId, stage, recipe, catalogue } = processing;
  const pageSettings = usePageSettings(projectId, pageId, stage);
  if (recipe === undefined) {
    return null;
  }
  const processorOf = (step: StepDraft): ProcessorSchema | undefined =>
    catalogue.find((processor) => processor.key === step.processorKey);
  const installed = new Set(catalogue.map((processor) => processor.key));
  const coming = roadmapOf(stage, installed);
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
        heading={labels.steps.title}
        showParams
        extraOf={(step) => (
          <>
            {isPlacement(step.processorKey) ? (
              <MeasureBook processing={processing} step={step} />
            ) : null}
            {pageId === undefined ? null : (
              <PageStepSettings
                processing={processing}
                step={step}
                processor={processorOf(step)}
                pageId={pageId}
                pageValues={pageValuesOf(pageSettings.data, step.stepId)}
                selected={selected}
              />
            )}
            {pageId === undefined || step.stepId === null ? null : (
              <StepReset
                processing={processing}
                pageId={pageId}
                stepId={step.stepId}
                title={processorOf(step)?.title ?? step.processorKey}
              />
            )}
          </>
        )}
        pageValuesOf={
          pageId === undefined ? undefined : (step) => pageValuesOf(pageSettings.data, step.stepId)
        }
        progress={progress}
      />
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm" className="w-fit" data-testid="step-add">
            <PlusIcon />
            {labels.steps.add}
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          {catalogue.map((processor) => (
            <DropdownMenuItem key={processor.key} onSelect={() => processing.add(processor)}>
              {processor.title}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>

      {coming.length === 0 ? null : (
        <ul aria-label={labels.soon.title} className="grid gap-2" data-testid="coming-steps">
          {coming.map(({ key, icon: Icon }) => (
            <li
              key={key}
              className="flex items-center gap-2 rounded-lg border border-dashed px-3 py-2 text-sm text-muted-foreground"
            >
              <Icon className="size-4" aria-hidden="true" />
              <span className="flex-1">{labels.soon.steps[key]}</span>
              <Badge variant="outline">{labels.soon.label}</Badge>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
