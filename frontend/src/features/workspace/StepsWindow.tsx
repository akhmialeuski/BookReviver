import { useSortable } from '@dnd-kit/sortable';
import { GripVerticalIcon, SettingsIcon, Trash2Icon, TriangleAlertIcon } from 'lucide-react';
import { useState } from 'react';
import type { AppliesTo, ProcessorSchema, StagePageSchema } from '@/api';
import type { OrderIssue } from '@/features/processing/order';
import { RecipeSaveBar } from '@/features/processing/RecipeSaveBar';
import { CONDITIONS, type StepDraft } from '@/features/processing/recipe';
import { StepSorter } from '@/features/processing/StepSorter';
import type { Processing } from '@/features/processing/useProcessing';
import { SaveProfileDialog } from '@/features/profiles/SaveProfileDialog';
import { ResetSteps } from '@/features/workspace/ResetSteps';
import { cn } from '@/shared/lib/utils';
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
import { Switch } from '@/shared/ui/switch';

/**
 * The window of the gear in the bar: the steps of the recipe as a list that the reader reorders by dragging, gives a
 * condition, switches on and off and removes, with the button that keeps the steps as a profile of the account and the one
 * that puts the default steps back. Profiles are applied elsewhere, so the window has no button for that.
 *
 * It is the recipe of the right panel in a window and edits the same draft, so a change made here is the change made
 * there, and the bar that saves it is the same bar. Nothing is written until it is pressed.
 */

const labels = MESSAGES.workspace.steps.gear;
const stepLabels = MESSAGES.processing.steps;

function StepRow({
  step,
  number,
  processor,
  issues,
  hover,
  onToggle,
  onRemove,
  onCondition,
}: {
  step: StepDraft;
  /** Its place in the recipe, from one. */
  number: number;
  processor: ProcessorSchema | undefined;
  /** What is wrong with the place of the step. */
  issues: readonly OrderIssue[];
  /** How a dragged step held over this one is received, or null when none is. */
  hover: 'usual' | 'free' | null;
  onToggle: () => void;
  onRemove: () => void;
  onCondition: (appliesTo: AppliesTo) => void;
}): React.JSX.Element {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: step.id,
  });
  const title = processor?.title ?? step.processorKey;
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
        'flex min-w-0 items-center gap-2 rounded-lg border bg-card px-1 py-1.5 text-card-foreground',
        isDragging && 'z-10 opacity-80 shadow-lg',
        hover === 'usual' && 'border-destructive bg-destructive/10',
        hover === 'free' && 'border-status-attention bg-status-attention/10',
        !step.enabled && 'text-muted-foreground',
      )}
      data-testid="window-step"
      data-processor={step.processorKey}
      data-step-id={step.stepId ?? undefined}
    >
      <Button
        variant="ghost"
        size="icon-sm"
        className="cursor-grab touch-none"
        aria-label={stepLabels.move(title)}
        title={stepLabels.move(title)}
        {...attributes}
        {...listeners}
      >
        <GripVerticalIcon />
      </Button>
      <span className="text-xs tabular-nums" aria-hidden="true">
        {number}
      </span>
      <div className="grid min-w-0 flex-1">
        <span className={cn('truncate text-sm font-medium', !step.enabled && 'line-through')}>
          {title}
        </span>
        {issues.length === 0 ? (
          processor === undefined || processor.summary === '' ? null : (
            <span className="truncate text-xs text-muted-foreground">{processor.summary}</span>
          )
        ) : (
          <span
            className={cn(
              'flex items-center gap-1 truncate text-xs',
              kind === 'required' ? 'text-destructive' : 'text-status-attention',
            )}
            title={issues.map((issue) => issue.reason).join(' ')}
            data-testid="window-step-order-mark"
            data-kind={kind}
          >
            <TriangleAlertIcon className="size-3.5 shrink-0" aria-hidden="true" />
            {stepLabels.order.marks[kind]}
          </span>
        )}
      </div>
      <select
        aria-label={stepLabels.condition.label(title)}
        title={stepLabels.condition.hint}
        data-testid="window-step-condition"
        className="h-8 shrink-0 rounded-md border border-input bg-background px-2 text-xs shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
        value={step.appliesTo}
        onChange={(event) => onCondition(event.target.value as AppliesTo)}
      >
        {CONDITIONS.map((condition) => (
          <option key={condition} value={condition}>
            {stepLabels.condition.options[condition]}
          </option>
        ))}
      </select>
      <Switch
        checked={step.enabled}
        aria-label={stepLabels.switchLabel(title)}
        data-testid="window-step-enabled"
        onCheckedChange={onToggle}
      />
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label={stepLabels.remove(title)}
        title={stepLabels.remove(title)}
        data-testid="window-step-remove"
        onClick={onRemove}
      >
        <Trash2Icon />
      </Button>
    </li>
  );
}

export function StepsWindow({
  processing,
  rows,
}: {
  processing: Processing;
  /** The rows of the stage, which the number of pages a save makes out of date is counted from. */
  rows: readonly StagePageSchema[];
}): React.JSX.Element | null {
  const { stage, steps, catalogue } = processing;
  const [savedAs, setSavedAs] = useState<string | null>(null);
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
        <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
          <span title={stepLabels.order.modeHint}>{stepLabels.order.modeLabel}</span>
          <Switch
            checked={processing.orderMode === 'free'}
            aria-label={stepLabels.order.modeLabel}
            data-testid="window-order-free"
            onCheckedChange={(free) => processing.setOrderMode(free ? 'free' : 'usual')}
          />
        </div>
        {steps.length === 0 ? (
          <p className="text-sm text-muted-foreground">{stepLabels.empty}</p>
        ) : (
          <StepSorter
            steps={steps}
            catalogue={catalogue}
            order={{
              mode: processing.orderMode,
              issues: processing.orderIssues,
              refusalOf: processing.refusalOf,
              onRestore: processing.restoreOrder,
            }}
            onMove={processing.move}
          >
            {(hoverOf) => (
              <ol className="grid gap-2" aria-label={stepLabels.title} data-testid="window-steps">
                {steps.map((step, index) => (
                  <StepRow
                    key={step.id}
                    step={step}
                    number={index + 1}
                    processor={catalogue.find((entry) => entry.key === step.processorKey)}
                    issues={processing.orderIssues.get(step.id) ?? []}
                    hover={hoverOf(step.id)}
                    onToggle={() => processing.toggle(step.id)}
                    onRemove={() => processing.remove(step.id)}
                    onCondition={(appliesTo) => processing.condition(step.id, appliesTo)}
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
            title={stepLabels.order.restoreHint}
            data-testid="window-restore-order"
            onClick={processing.restoreOrder}
          >
            {stepLabels.order.restore}
          </Button>
        )}
        <RecipeSaveBar processing={processing} rows={rows} />
        <div className="flex flex-wrap gap-2">
          <SaveProfileDialog processing={processing} onSaved={setSavedAs} />
          <ResetSteps processing={processing} rows={rows} />
        </div>
        {savedAs === null ? null : (
          <p className="text-xs text-muted-foreground" data-testid="profile-saved">
            {MESSAGES.profiles.save.saved(savedAs)}
          </p>
        )}
      </DialogContent>
    </Dialog>
  );
}
