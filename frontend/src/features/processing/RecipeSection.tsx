import { CopyPlusIcon, PlusIcon } from 'lucide-react';
import type { StagePageSchema } from '@/api';
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
  useSaveRecipe,
} from '@/features/processing/queries';
import { bodyOf, pagesToGoStale } from '@/features/processing/recipe';
import { StepList, type StepRunControl } from '@/features/processing/StepList';
import { RunScope } from '@/features/processing/scope';
import { passedPages } from '@/features/processing/stepRuns';
import { UsedFor } from '@/features/processing/UsedFor';
import type { Processing } from '@/features/processing/useProcessing';
import type { StageRun } from '@/features/processing/useStageRun';
import { countsOf } from '@/features/processing/variants';
import { ProfileActions } from '@/features/profiles/ProfileActions';
import { ProfileMenu } from '@/features/profiles/ProfileMenu';
import { roadmapOf } from '@/features/stages/roadmap';
import { useStageSummaries } from '@/features/workspace/queries';
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
 * The recipe of a stage in the panel: which recipe is shown, its steps with their settings, the steps that can be added
 * and the ones that are coming, and the bar that saves what was changed.
 *
 * Nothing is saved while a step is edited. The draft lives in the state of the screen, so a preview can use it, and the
 * recipe is written only by the button, which says first how many pages the save makes out of date.
 *
 * When a page is open, a step that is open also shows what that page changes for the step, and marks those fields in
 * its form.
 */

const labels = MESSAGES.processing;

export function RecipeSection({
  processing,
  rows,
  run,
  pageId,
  onManageProfiles,
}: {
  processing: Processing;
  rows: readonly StagePageSchema[];
  /** The run of the stage, which the steps use to run the recipe up to one of them. Absent for steps that only edit. */
  run?: StageRun;
  /** The page that is open, whose own settings of the steps are shown, or absent when none is. */
  pageId?: string;
  /** Opens the library of profiles, which the profile menu offers when it is given. */
  onManageProfiles?: () => void;
}): React.JSX.Element | null {
  const { projectId, stage, recipe, steps, catalogue } = processing;
  const save = useSaveRecipe(projectId, stage);
  const create = useCreateVariant(projectId, stage);
  const activate = useActivateRecipe(projectId, stage);
  const rules = useRules(projectId, stage, recipe !== undefined);
  const summaries = useStageSummaries(projectId);
  const pageSettings = usePageSettings(projectId, pageId, stage);
  if (recipe === undefined) {
    return null;
  }

  const processedBy = (id: string): number => rows.filter((row) => row.recipe_id === id).length;
  const counts = countsOf(
    summaries.data?.find((entry) => entry.stage === stage),
    processing.recipes,
    labels.recipe.countOf,
  );
  const installed = new Set(catalogue.map((processor) => processor.key));
  const coming = roadmapOf(stage, installed);
  const error = save.error ?? create.error ?? activate.error;
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
      <ProfileActions processing={processing} />

      <UsedFor
        projectId={projectId}
        recipe={recipe}
        recipes={processing.recipes}
        rules={rules.data ?? []}
        pages={processedBy(recipe.id)}
      />

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
                processor={catalogue.find((processor) => processor.key === step.processorKey)}
                pageId={pageId}
                pageValues={pageValuesOf(pageSettings.data, step.stepId)}
              />
            )}
            {pageId === undefined ? null : (
              <PageStepHistory
                processing={processing}
                step={step}
                processor={catalogue.find((processor) => processor.key === step.processorKey)}
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

      {processing.dirty ? (
        <div className="grid gap-2 rounded-lg border bg-muted/40 p-3" data-testid="recipe-save-bar">
          <p className="text-sm">{labels.save.unsaved}</p>
          {pagesToGoStale(rows, recipe.id) > 0 ? (
            <p className="text-sm text-status-attention" data-testid="recipe-stale-warning">
              {labels.save.staleWarning(pagesToGoStale(rows, recipe.id))}
            </p>
          ) : null}
          {processing.valid ? null : (
            <p className="text-sm text-destructive">{labels.steps.outOfLimits}</p>
          )}
          {processing.refused.length === 0 ? null : (
            <p className="text-sm text-destructive" data-testid="recipe-order-blocked">
              {labels.steps.order.blocked}
            </p>
          )}
          <div className="flex gap-2">
            <Button
              size="sm"
              disabled={!processing.valid || processing.refused.length > 0 || save.isPending}
              data-testid="recipe-save"
              onClick={() =>
                save.mutate({
                  path: { project_id: projectId, stage, recipe_id: recipe.id },
                  body: { name: recipe.name, steps: bodyOf(steps), order: processing.orderMode },
                })
              }
            >
              {save.isPending ? labels.save.saving : labels.save.save}
            </Button>
            <Button variant="ghost" size="sm" onClick={processing.discard}>
              {labels.save.discard}
            </Button>
          </div>
        </div>
      ) : null}
      {error === null ? null : <ErrorAlert message={describeError(error)} />}
    </section>
  );
}
