import { CopyPlusIcon, PlusIcon } from 'lucide-react';
import type { StagePageSchema } from '@/api';
import { isPlacement } from '@/features/editors/placement';
import { MeasureBook } from '@/features/processing/MeasureBook';
import {
  useActivateRecipe,
  useCreateVariant,
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

/**
 * The recipe of a stage in the panel: which recipe is shown, its steps with their settings, the steps that can be added
 * and the ones that are coming, and the bar that saves what was changed.
 *
 * Nothing is saved while a step is edited. The draft lives in the state of the screen, so a preview can use it, and the
 * recipe is written only by the button, which says first how many pages the save makes out of date.
 */

const labels = MESSAGES.processing;

export function RecipeSection({
  processing,
  rows,
  run,
}: {
  processing: Processing;
  rows: readonly StagePageSchema[];
  /** The run of the stage, which the steps use to run the recipe up to one of them. Absent for steps that only edit. */
  run?: StageRun;
}): React.JSX.Element | null {
  const { projectId, stage, recipe, steps, catalogue } = processing;
  const save = useSaveRecipe(projectId, stage);
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
    <section className="grid gap-3" aria-label={labels.recipe.label}>
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
                body: { name: labels.recipe.copyName(recipe.name), steps: bodyOf(steps) },
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

      <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
        {labels.steps.title}
      </h3>
      <StepList
        steps={steps}
        catalogue={catalogue}
        openId={processing.openId}
        extraOf={(step) =>
          isPlacement(step.processorKey) ? (
            <MeasureBook processing={processing} step={step} />
          ) : null
        }
        run={stepRun}
        onOpen={processing.open}
        onMove={processing.move}
        onToggle={processing.toggle}
        onRemove={processing.remove}
        onChange={processing.change}
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
          <div className="flex gap-2">
            <Button
              size="sm"
              disabled={!processing.valid || save.isPending}
              data-testid="recipe-save"
              onClick={() =>
                save.mutate({
                  path: { project_id: projectId, stage, recipe_id: recipe.id },
                  body: { name: recipe.name, steps: bodyOf(steps) },
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
