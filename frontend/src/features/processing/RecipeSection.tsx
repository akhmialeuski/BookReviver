import { CopyPlusIcon, PlusIcon } from 'lucide-react';
import type { ProcessorSchema, StagePageSchema } from '@/api';
import { isPlacement } from '@/features/editors/placement';
import { MeasureBook } from '@/features/processing/MeasureBook';
import { PageStepHistory } from '@/features/processing/PageStepHistory';
import { PageStepSettings } from '@/features/processing/PageStepSettings';
import { pageValuesOf } from '@/features/processing/pageSettings';
import {
  useActivateRecipe,
  useCreateVariant,
  usePageSettings,
  useRules,
} from '@/features/processing/queries';
import { RecipeSaveBar } from '@/features/processing/RecipeSaveBar';
import { bodyOf, type StepDraft } from '@/features/processing/recipe';
import { StepList, type StepRunControl } from '@/features/processing/StepList';
import { StepReset } from '@/features/processing/StepReset';
import { RunScope } from '@/features/processing/scope';
import { passedPages } from '@/features/processing/stepRuns';
import { UsedFor } from '@/features/processing/UsedFor';
import type { Processing } from '@/features/processing/useProcessing';
import type { StageRun } from '@/features/processing/useStageRun';
import { countsOf } from '@/features/processing/variants';
import { ProfileMenu } from '@/features/profiles/ProfileMenu';
import { roadmapOf } from '@/features/stages/roadmap';
import { useStageSummaries } from '@/features/workspace/queries';
import { hasStepBar } from '@/features/workspace/steps';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Badge } from '@/shared/ui/badge';
import { Button } from '@/shared/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu';
import { ErrorAlert } from '@/shared/ui/error-alert';
import { Switch } from '@/shared/ui/switch';

/**
 * The recipe of a stage in the panel: which recipe is shown, what it is used for, and the bar that saves what was changed.
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
  /** The run of the stage, which the steps use to run the recipe up to one of them. Absent for steps that only edit. */
  run?: StageRun;
  /** The page that is open, whose own settings of the steps are shown, or absent when none is. */
  pageId?: string;
  /** Opens the library of profiles, which the profile menu offers when it is given. */
  onManageProfiles?: () => void;
  /** The pages selected in the grid, which a setting of the open page can be carried over to. */
  selected?: ReadonlySet<string>;
}): React.JSX.Element | null {
  const { projectId, stage, recipe, steps } = processing;
  const create = useCreateVariant(projectId, stage);
  const activate = useActivateRecipe(projectId, stage);
  const rules = useRules(projectId, stage, recipe !== undefined);
  const summaries = useStageSummaries(projectId);
  if (recipe === undefined) {
    return null;
  }

  const processedBy = (id: string): number => rows.filter((row) => row.recipe_id === id).length;
  const counts = countsOf(
    summaries.data?.find((entry) => entry.stage === stage),
    processing.recipes,
    labels.recipe.countOf,
  );
  const error = create.error ?? activate.error;

  return (
    <section className="grid grid-cols-1 gap-3" aria-label={labels.recipe.label}>
      <div className="flex min-h-6 items-center justify-between gap-2">
        <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
          {labels.recipe.label}
        </h3>
        {recipe.active ? (
          <Badge variant="secondary" data-testid="recipe-active">
            {labels.recipe.activeBadge(processedBy(recipe.id))}
          </Badge>
        ) : null}
      </div>
      {counts.length < 2 ? null : (
        <p
          className="text-xs text-muted-foreground"
          title={labels.recipe.counts}
          data-testid="variant-counts"
        >
          {counts.join(' · ')}
        </p>
      )}
      <ProfileMenu processing={processing} onManage={onManageProfiles} />
      <select
        aria-label={labels.recipe.choose}
        data-testid="recipe-select"
        className="h-9 w-full min-w-0 rounded-md border border-input bg-background px-3 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
        value={recipe.id}
        onChange={(event) => processing.chooseRecipe(event.target.value)}
      >
        {processing.recipes.map((entry) => (
          <option key={entry.id} value={entry.id}>
            {labels.recipe.option(entry.name, entry.active, processedBy(entry.id))}
          </option>
        ))}
      </select>
      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          size="sm"
          title={labels.recipe.newRecipeHint}
          disabled={create.isPending}
          data-testid="recipe-new"
          onClick={() =>
            create.mutate(
              {
                path: { project_id: projectId, stage },
                body: {
                  name: labels.recipe.copyName(recipe.name),
                  steps: bodyOf(steps),
                  order: processing.orderMode,
                },
              },
              { onSuccess: (created) => processing.chooseRecipe(created.id) },
            )
          }
        >
          <CopyPlusIcon />
          {labels.recipe.newRecipe}
        </Button>
        {recipe.active ? null : (
          <Button
            variant="outline"
            size="sm"
            title={labels.recipe.useHint}
            disabled={activate.isPending || processing.dirty}
            data-testid="recipe-use"
            onClick={() =>
              activate.mutate({
                path: { project_id: projectId, stage, recipe_id: recipe.id },
              })
            }
          >
            {labels.recipe.use}
          </Button>
        )}
      </div>

      <UsedFor
        projectId={projectId}
        recipe={recipe}
        recipes={processing.recipes}
        rules={rules.data ?? []}
        pages={processedBy(recipe.id)}
      />

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
      {error === null ? null : <ErrorAlert message={describeError(error)} />}
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
  const { projectId, stage, recipe, steps, catalogue } = processing;
  const pageSettings = usePageSettings(projectId, pageId, stage);
  if (recipe === undefined) {
    return null;
  }
  const processorOf = (step: StepDraft): ProcessorSchema | undefined =>
    catalogue.find((processor) => processor.key === step.processorKey);
  const installed = new Set(catalogue.map((processor) => processor.key));
  const coming = roadmapOf(stage, installed);
  const stepRun: StepRunControl | undefined =
    run === undefined
      ? undefined
      : {
          choices: run.choices,
          describe: run.describe,
          disabled: run.disabled,
          passed: (index) => passedPages(rows, recipe.id, index),
          total: run.choices.find(({ scope }) => scope === RunScope.All)?.count ?? 0,
          onRun: (index, scope) => run.start(scope, index),
        };

  return (
    <>
      <div className="flex min-h-6 items-center justify-between gap-2">
        <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
          {labels.steps.title}
        </h3>
        <div
          className="flex items-center gap-2 text-xs text-muted-foreground"
          title={labels.steps.order.modeHint}
        >
          <span>{labels.steps.order.modeLabel}</span>
          <Switch
            checked={processing.orderMode === 'free'}
            aria-label={labels.steps.order.modeLabel}
            data-testid="order-free"
            onCheckedChange={(free) => processing.setOrderMode(free ? 'free' : 'usual')}
          />
        </div>
      </div>
      <StepList
        steps={steps}
        catalogue={catalogue}
        openId={processing.openId}
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
            {pageId === undefined ? null : (
              <PageStepHistory
                processing={processing}
                step={step}
                processor={processorOf(step)}
                pageId={pageId}
              />
            )}
          </>
        )}
        pageValuesOf={
          pageId === undefined ? undefined : (step) => pageValuesOf(pageSettings.data, step.stepId)
        }
        run={stepRun}
        order={{
          mode: processing.orderMode,
          issues: processing.orderIssues,
          refusalOf: processing.refusalOf,
          onRestore: processing.restoreOrder,
        }}
        onOpen={processing.open}
        onMove={processing.move}
        onToggle={processing.toggle}
        onRemove={processing.remove}
        onChange={processing.change}
        onCondition={processing.condition}
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
