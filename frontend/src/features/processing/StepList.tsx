import { useSortable } from '@dnd-kit/sortable';
import {
  ChevronDownIcon,
  ChevronRightIcon,
  GripVerticalIcon,
  Trash2Icon,
  TriangleAlertIcon,
} from 'lucide-react';
import type { OrderMode, ProcessorSchema } from '@/api';
import type { OrderIssue } from '@/features/processing/order';
import { ParamsForm } from '@/features/processing/ParamsForm';
import type { PageValues } from '@/features/processing/pageSettings';
import type { StepDraft } from '@/features/processing/recipe';
import { StepSorter } from '@/features/processing/StepSorter';
import { fitsSchema, formSchemaOf } from '@/features/processing/schema';
import type { Processing } from '@/features/processing/useProcessing';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { Switch } from '@/shared/ui/switch';

/**
 * The steps of the recipe as a list the reader reorders by dragging a handle or with the keyboard, switches on and off,
 * removes and opens to change their settings. It is the one editor of that list: the panel of a stage
 * without a step bar and the window of the gear of a stage with one both draw it, over the same draft.
 *
 * The switch of the order of the steps and the button that puts them back in their usual order belong to it too, since
 * the marks of a step out of its place are drawn here.
 *
 * A step shows the title of its processor from the catalogue. A step whose processor is not installed on this machine
 * still shows, with a note, so a recipe is never edited blind and the step can still be removed.
 *
 * When the list is given the progress of the pages, each step of a recipe of several says how many pages already passed
 * it. A run starts from the foot of the panel and not from here. When it is given the open page, the form of a step shows
 * under each setting the values the page and the parts of the pages have for it. A step shows the form of its settings only where they are not set elsewhere.
 */

const labels = MESSAGES.processing.steps;

/** What the steps need to show how far the pages have come. */
export interface StepProgress {
  /** How many pages passed the step with this index. */
  passed: (index: number) => number;
  /** The pages a step can be passed by. */
  total: number;
}

function StepCard({
  step,
  number,
  processor,
  open,
  extra,
  progress,
  values,
  issues,
  hover,
  showParams,
  processing,
}: {
  step: StepDraft;
  /** Its place in the recipe, from one. */
  number: number;
  processor: ProcessorSchema | undefined;
  open: boolean;
  /** What the step shows under its settings when it is open, such as the button that measures the book. */
  extra: React.ReactNode;
  progress: StepProgress | undefined;
  /** The open page, whose values for this step and the parts of the pages the form shows under each setting. */
  values: PageValues | undefined;
  /** What is wrong with the place of this step. */
  issues: readonly OrderIssue[];
  /** How a dragged step that is over this one is received, or null when nothing is held over it. */
  hover: OrderMode | null;
  /** Whether the open step draws the form of its settings, which a stage with a step bar sets in the panel of the step. */
  showParams: boolean;
  processing: Processing;
}): React.JSX.Element {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: step.id,
  });
  const title = processor?.title ?? step.processorKey;
  const outOfLimits =
    processor !== undefined && !fitsSchema(formSchemaOf(processor.parameters), step.params);
  const kind = issues.some((issue) => issue.kind === 'required') ? 'required' : 'usual';

  return (
    <li
      ref={setNodeRef}
      style={{
        transform:
          transform === null ? undefined : `translate3d(${transform.x}px, ${transform.y}px, 0)`,
        transition,
      }}
      className={cn(
        'min-w-0 rounded-lg border bg-card text-card-foreground',
        isDragging && 'z-10 opacity-80 shadow-lg',
        outOfLimits && 'border-destructive/60',
        hover === 'usual' && 'border-destructive bg-destructive/10',
        hover === 'free' && 'border-status-attention bg-status-attention/10',
      )}
      data-testid="recipe-step"
      data-processor={step.processorKey}
    >
      <div className="flex items-center gap-1 px-1 py-1.5">
        <Button
          variant="ghost"
          size="icon-sm"
          className="cursor-grab touch-none"
          aria-label={labels.move(title)}
          title={labels.move(title)}
          {...attributes}
          {...listeners}
        >
          <GripVerticalIcon />
        </Button>
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-1 rounded-md px-1 py-1 text-left text-sm font-medium outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
          aria-expanded={open}
          aria-label={open ? labels.hideSettings(title) : labels.showSettings(title)}
          data-testid="step-toggle"
          onClick={() => processing.open(open ? undefined : step.id)}
        >
          {open ? <ChevronDownIcon className="size-4" /> : <ChevronRightIcon className="size-4" />}
          <span className="truncate">{labels.step(number, title)}</span>
        </button>
        <Switch
          checked={step.enabled}
          aria-label={labels.switchLabel(title)}
          data-testid="step-enabled"
          onCheckedChange={() => processing.toggle(step.id)}
        />
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={labels.remove(title)}
          title={labels.remove(title)}
          data-testid="step-remove"
          onClick={() => processing.remove(step.id)}
        >
          <Trash2Icon />
        </Button>
      </div>
      {issues.length === 0 && (progress === undefined || !step.enabled) ? null : (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 pb-1.5 text-xs">
          {progress === undefined || !step.enabled ? null : (
            <p
              className="text-muted-foreground"
              title={labels.passedHint}
              data-testid="step-passed"
            >
              {labels.passed(progress.passed(number - 1), progress.total)}
            </p>
          )}
          {issues.length === 0 ? null : (
            <span
              className={cn(
                'flex shrink-0 items-center gap-1',
                kind === 'required' ? 'text-destructive' : 'text-status-attention',
              )}
              title={issues.map((issue) => issue.reason).join(' ')}
              data-testid="step-order-mark"
              data-kind={kind}
            >
              <TriangleAlertIcon className="size-3.5" aria-hidden="true" />
              {labels.order.marks[kind]}
            </span>
          )}
        </div>
      )}
      {issues.length === 0 || open ? null : (
        <p
          className={cn(
            'px-3 pb-1.5 text-xs break-words',
            kind === 'required' ? 'text-destructive' : 'text-status-attention',
          )}
          data-testid="step-order-reason"
        >
          {issues[0]?.reason}
        </p>
      )}
      {open ? (
        <div className="grid gap-2 border-t px-3 py-3">
          {step.enabled ? null : (
            <p className="text-xs text-muted-foreground">{labels.switchedOff}</p>
          )}
          {issues.length === 0 ? null : (
            <div className="grid gap-2" data-testid="step-order-details">
              {issues.map((issue) => (
                <p
                  key={`${issue.otherId}|${issue.reason}`}
                  className={cn(
                    'text-xs break-words',
                    issue.kind === 'required' ? 'text-destructive' : 'text-status-attention',
                  )}
                >
                  {issue.reason}
                </p>
              ))}
            </div>
          )}
          {processor === undefined ? (
            <p className="text-sm text-muted-foreground">{labels.unknownProcessor}</p>
          ) : showParams ? (
            <ParamsForm
              processor={processor}
              params={step.params}
              values={
                values === undefined || step.stepId === null
                  ? undefined
                  : { page: values, stepId: step.stepId }
              }
              onChange={(params) => processing.change(step.id, params)}
            />
          ) : processor.summary === '' ? null : (
            <p className="text-xs text-muted-foreground">{processor.summary}</p>
          )}
          {extra}
        </div>
      ) : null}
    </li>
  );
}

export function StepList({
  processing,
  heading,
  showParams,
  extraOf,
  progress,
  values,
}: {
  processing: Processing;
  /** A heading drawn beside the switch of the order, or absent where the list stands under a title of its own. */
  heading?: string;
  /** Whether an open step draws the form of its settings, which a stage with a step bar sets in the panel of the step. */
  showParams: boolean;
  /** What a step shows under its settings when it is open, or nothing. */
  extraOf?: (step: StepDraft) => React.ReactNode;
  /** How far the pages have come, or absent for a list that only edits. A recipe of one step has no such thing. */
  progress?: StepProgress;
  /** The open page and what it and the parts of the pages have for the steps, or absent when no page is open. */
  values?: PageValues;
}): React.JSX.Element {
  const { steps, catalogue } = processing;
  return (
    <>
      <div className="flex min-h-6 items-center justify-between gap-2">
        {heading === undefined ? null : (
          <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
            {heading}
          </h3>
        )}
        <div
          className="ml-auto flex items-center gap-2 text-xs text-muted-foreground"
          title={labels.order.modeHint}
        >
          <span>{labels.order.modeLabel}</span>
          <Switch
            checked={processing.orderMode === 'free'}
            aria-label={labels.order.modeLabel}
            data-testid="order-free"
            onCheckedChange={(free) => processing.setOrderMode(free ? 'free' : 'usual')}
          />
        </div>
      </div>
      {steps.length === 0 ? (
        <p className="text-sm text-muted-foreground">{labels.empty}</p>
      ) : (
        <StepSorter processing={processing}>
          {(hoverOf) => (
            <ol
              aria-label={labels.title}
              className="grid grid-cols-1 gap-2"
              data-testid="recipe-steps"
            >
              {steps.map((step, index) => (
                <StepCard
                  key={step.id}
                  step={step}
                  number={index + 1}
                  processor={catalogue.find((entry) => entry.key === step.processorKey)}
                  open={step.id === processing.openId}
                  extra={extraOf?.(step) ?? null}
                  progress={steps.length > 1 ? progress : undefined}
                  values={values}
                  issues={processing.orderIssues.get(step.id) ?? []}
                  hover={hoverOf(step.id)}
                  showParams={showParams}
                  processing={processing}
                />
              ))}
            </ol>
          )}
        </StepSorter>
      )}
      {processing.orderIssues.size === 0 ? null : (
        <Button
          variant="outline"
          size="sm"
          className="w-fit"
          title={labels.order.restoreHint}
          data-testid="steps-restore-order"
          onClick={processing.restoreOrder}
        >
          {labels.order.restore}
        </Button>
      )}
    </>
  );
}
