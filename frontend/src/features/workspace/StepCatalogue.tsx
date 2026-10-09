import { PlusIcon } from 'lucide-react';
import { useState } from 'react';
import type { ProcessorSchema, StagePageSchema } from '@/api';
import { refusalOfAdd, usualPlace } from '@/features/processing/order';
import { useSaveRecipe } from '@/features/processing/queries';
import { addStep, bodyOf, draftOf, pagesToGoStale } from '@/features/processing/recipe';
import type { Processing } from '@/features/processing/useProcessing';
import { roadmapOf } from '@/features/stages/roadmap';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Badge } from '@/shared/ui/badge';
import { Button } from '@/shared/ui/button';
import { ErrorAlert } from '@/shared/ui/error-alert';
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/ui/popover';

/**
 * The catalogue of the steps a stage can have, which the plus button opens, in the bar of a stage that has one and under
 * the list of steps of a stage that has none: every processor of the stage
 * with what it does, and the steps that are planned and not built, marked "Soon".
 *
 * Choosing a step adds it to the saved recipe at once, and it is shown in the bar and opened. A step has no identifier
 * until the server gives it one when the recipe is saved, and the bar lists only steps that have one, so a step that
 * waited in the draft could not be opened. The same processor may be chosen again: each instance keeps its own settings,
 * condition and edits. The step stands where its processor usually does, and a place that the usual order refuses is
 * told to the reader and nothing is sent. The button waits while the draft of the panel holds changes, since saving the
 * recipe would save them too.
 */

const labels = MESSAGES.workspace.steps.catalogue;

export function StepCatalogue({
  processing,
  rows,
  onAdded,
}: {
  processing: Processing;
  /** The rows of the stage, which the number of pages the new step makes out of date is counted from. */
  rows: readonly StagePageSchema[];
  /** Called with the identifier the server gave the new step and its place from zero, so the step can be opened. */
  onAdded: (stepId: string, index: number) => void;
}): React.JSX.Element | null {
  const { projectId, stage, recipe, catalogue } = processing;
  const save = useSaveRecipe(projectId, stage);
  const [open, setOpen] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  if (recipe === undefined) {
    return null;
  }
  const saved = draftOf(recipe);
  const coming = roadmapOf(stage, new Set(catalogue.map((processor) => processor.key)));
  const stale = pagesToGoStale(rows, recipe.id);

  const add = (processor: ProcessorSchema): void => {
    const index = usualPlace(saved, catalogue, processor);
    const issue =
      processing.orderMode === 'usual'
        ? refusalOfAdd(saved, catalogue, processor, index)
        : undefined;
    if (issue !== undefined) {
      setRefusal(issue.reason);
      return;
    }
    setRefusal(null);
    save.mutate(
      {
        path: { project_id: projectId, stage, recipe_id: recipe.id },
        body: { steps: bodyOf(addStep(saved, processor, index)), order: processing.orderMode },
      },
      {
        onSuccess: (updated) => {
          setOpen(false);
          const stepId = updated.steps[index]?.step_id;
          if (stepId !== undefined) {
            onAdded(stepId, index);
          }
        },
      },
    );
  };

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        setRefusal(null);
        save.reset();
      }}
    >
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="icon-sm"
          className="shrink-0 border-dashed"
          aria-label={labels.open}
          title={labels.openHint}
          data-testid="step-catalogue"
        >
          <PlusIcon aria-hidden="true" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[28rem] p-2" data-testid="step-catalogue-list">
        <p className="px-2 py-1 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
          {labels.title(MESSAGES.stages.names[stage])}
        </p>
        <ul className="grid gap-0.5">
          {catalogue.map((processor) => (
            <li key={processor.key}>
              <button
                type="button"
                className="grid w-full rounded-md px-2 py-1.5 text-left outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50"
                disabled={processing.dirty || save.isPending}
                data-testid="catalogue-add"
                data-processor={processor.key}
                onClick={() => add(processor)}
              >
                <span className="text-sm font-medium">{processor.title}</span>
                {processor.summary === '' ? null : (
                  <span className="text-xs text-muted-foreground">{processor.summary}</span>
                )}
              </button>
            </li>
          ))}
          {coming.map(({ key }) => (
            <li
              key={key}
              className="flex items-center justify-between gap-2 px-2 py-1.5 text-muted-foreground"
              data-testid="catalogue-soon"
            >
              <span className="text-sm">{MESSAGES.processing.soon.steps[key]}</span>
              <Badge variant="outline">{MESSAGES.processing.soon.label}</Badge>
            </li>
          ))}
        </ul>
        <div className="grid gap-1 border-t px-2 pt-2 text-xs text-muted-foreground">
          <p>{labels.hint}</p>
          {processing.dirty ? (
            <p className="text-status-attention" data-testid="catalogue-dirty">
              {labels.saveFirst}
            </p>
          ) : stale > 0 ? (
            <p data-testid="catalogue-stale">{labels.stale(stale)}</p>
          ) : null}
          {refusal === null ? null : (
            <p className="text-destructive" role="alert" data-testid="catalogue-refused">
              {labels.refused(refusal)}
            </p>
          )}
        </div>
        {save.error === null ? null : <ErrorAlert message={describeError(save.error)} />}
      </PopoverContent>
    </Popover>
  );
}
