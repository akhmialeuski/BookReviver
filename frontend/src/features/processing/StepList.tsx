import {
  type Announcements,
  closestCenter,
  DndContext,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
} from '@dnd-kit/core';
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import {
  ChevronDownIcon,
  ChevronRightIcon,
  GripVerticalIcon,
  StepForwardIcon,
  Trash2Icon,
} from 'lucide-react';
import type { AppliesTo, ProcessorSchema } from '@/api';
import { ParamsForm } from '@/features/processing/ParamsForm';
import { CONDITIONS, type StepDraft } from '@/features/processing/recipe';
import { fitsSchema, formSchemaOf } from '@/features/processing/schema';
import type { RunScope, ScopeChoice } from '@/features/processing/scope';
import { canRunThrough } from '@/features/processing/stepRuns';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu';
import { Switch } from '@/shared/ui/switch';

/**
 * The steps of the recipe as a list the reader reorders by dragging a handle or with the keyboard, switches on and off,
 * removes, and opens to change their settings.
 *
 * A step shows the title of its processor from the catalogue. A step whose processor is not installed on this machine
 * still shows, with a note, so a recipe is never edited blind and the step can still be removed.
 *
 * When the list is given a way to run, each step of a recipe of several offers "Run up to here" over the scopes of a run,
 * and says how many pages already passed it.
 */

const labels = MESSAGES.processing.steps;

/** What the steps need to run the recipe up to one of them and to show how far the pages have come. */
export interface StepRunControl {
  /** The scopes of the menu with the number of pages each covers now. */
  choices: readonly ScopeChoice[];
  describe: (scope: RunScope, count: number) => string;
  /** Whether a run cannot be asked for now. */
  disabled: boolean;
  /** How many pages passed the step with this index. */
  passed: (index: number) => number;
  /** The pages a step can be passed by. */
  total: number;
  /** Run the recipe up to the step with this index, over a scope. */
  onRun: (index: number, scope: RunScope) => void;
}

function StepCard({
  step,
  number,
  processor,
  open,
  extra,
  runnable,
  run,
  onOpen,
  onToggle,
  onRemove,
  onChange,
  onCondition,
}: {
  step: StepDraft;
  /** Its place in the recipe, from one. */
  number: number;
  processor: ProcessorSchema | undefined;
  open: boolean;
  /** What the step shows under its settings when it is open, such as the button that measures the book. */
  extra: React.ReactNode;
  /** Whether a step or one before it is on, so the recipe can be run up to this one. */
  runnable: boolean;
  run: StepRunControl | undefined;
  onOpen: (open: boolean) => void;
  onToggle: () => void;
  onRemove: () => void;
  onChange: (params: Record<string, unknown>) => void;
  onCondition: (appliesTo: AppliesTo) => void;
}): React.JSX.Element {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: step.id,
  });
  const title = processor?.title ?? step.processorKey;
  const outOfLimits =
    processor !== undefined && !fitsSchema(formSchemaOf(processor.parameters), step.params);

  return (
    <li
      ref={setNodeRef}
      style={{
        transform:
          transform === null ? undefined : `translate3d(${transform.x}px, ${transform.y}px, 0)`,
        transition,
      }}
      className={cn(
        'rounded-lg border bg-card text-card-foreground',
        isDragging && 'z-10 opacity-80 shadow-lg',
        outOfLimits && 'border-destructive/60',
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
          onClick={() => onOpen(!open)}
        >
          {open ? <ChevronDownIcon className="size-4" /> : <ChevronRightIcon className="size-4" />}
          <span className="truncate">{labels.step(number, title)}</span>
        </button>
        {run === undefined ? null : (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={`${labels.runThrough}: ${title}`}
                title={runnable ? labels.runThroughHint(title) : labels.runThroughOff}
                disabled={!runnable || run.disabled}
                data-testid="step-run"
              >
                <StepForwardIcon />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuLabel>{labels.runThrough}</DropdownMenuLabel>
              {run.choices.map(({ scope, count }) => (
                <DropdownMenuItem
                  key={scope}
                  disabled={count === 0}
                  data-testid={`step-run-${scope}`}
                  onSelect={() => run.onRun(number - 1, scope)}
                >
                  {run.describe(scope, count)}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        )}
        <Switch
          checked={step.enabled}
          aria-label={labels.switchLabel(title)}
          data-testid="step-enabled"
          onCheckedChange={onToggle}
        />
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={labels.remove(title)}
          title={labels.remove(title)}
          data-testid="step-remove"
          onClick={onRemove}
        >
          <Trash2Icon />
        </Button>
      </div>
      {run === undefined || !step.enabled ? null : (
        <p
          className="px-3 pb-1.5 text-xs text-muted-foreground"
          title={labels.passedHint}
          data-testid="step-passed"
        >
          {labels.passed(run.passed(number - 1), run.total)}
        </p>
      )}
      {open ? (
        <div className="grid gap-2 border-t px-3 py-3">
          {step.enabled ? null : (
            <p className="text-xs text-muted-foreground">{labels.switchedOff}</p>
          )}
          <label className="grid gap-1 text-xs text-muted-foreground" title={labels.condition.hint}>
            {labels.condition.label(title)}
            <select
              aria-label={labels.condition.label(title)}
              data-testid="step-condition"
              className="h-9 w-full min-w-0 rounded-md border border-input bg-background px-3 text-sm text-foreground shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
              value={step.appliesTo}
              onChange={(event) => onCondition(event.target.value as AppliesTo)}
            >
              {CONDITIONS.map((condition) => (
                <option key={condition} value={condition}>
                  {labels.condition.options[condition]}
                </option>
              ))}
            </select>
          </label>
          {processor === undefined ? (
            <p className="text-sm text-muted-foreground">{labels.unknownProcessor}</p>
          ) : (
            <ParamsForm processor={processor} params={step.params} onChange={onChange} />
          )}
          {extra}
        </div>
      ) : null}
    </li>
  );
}

export function StepList({
  steps,
  catalogue,
  openId,
  extraOf,
  run,
  onOpen,
  onMove,
  onToggle,
  onRemove,
  onChange,
  onCondition,
}: {
  steps: readonly StepDraft[];
  catalogue: readonly ProcessorSchema[];
  openId: string | undefined;
  /** What a step shows under its settings when it is open, or nothing. */
  extraOf?: (step: StepDraft) => React.ReactNode;
  /** How to run the recipe up to a step, or absent for a list that only edits. A recipe of one step has no such thing. */
  run?: StepRunControl;
  onOpen: (id: string | undefined) => void;
  onMove: (activeId: string, overId: string) => void;
  onToggle: (id: string) => void;
  onRemove: (id: string) => void;
  onChange: (id: string, params: Record<string, unknown>) => void;
  onCondition: (id: string, appliesTo: AppliesTo) => void;
}): React.JSX.Element {
  const sensors = useSensors(
    // A press that moves a little is a click on the handle, not the start of a drag
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );
  const nameOf = (id: string | number): string => {
    const step = steps.find((entry) => entry.id === id);
    return catalogue.find((entry) => entry.key === step?.processorKey)?.title ?? String(id);
  };
  const announcements: Announcements = {
    onDragStart: ({ active }) => labels.drag.pickedUp(nameOf(active.id)),
    onDragOver: ({ over }) => (over === null ? undefined : labels.drag.over(nameOf(over.id))),
    onDragEnd: ({ active }) => labels.drag.dropped(nameOf(active.id)),
    onDragCancel: () => labels.drag.cancelled,
  };

  if (steps.length === 0) {
    return <p className="text-sm text-muted-foreground">{labels.empty}</p>;
  }
  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      accessibility={{
        announcements,
        screenReaderInstructions: { draggable: labels.drag.instructions },
      }}
      onDragEnd={({ active, over }) => {
        if (over !== null) {
          onMove(String(active.id), String(over.id));
        }
      }}
    >
      <SortableContext items={steps.map((step) => step.id)} strategy={verticalListSortingStrategy}>
        <ol aria-label={labels.title} className="grid gap-2" data-testid="recipe-steps">
          {steps.map((step, index) => (
            <StepCard
              key={step.id}
              step={step}
              number={index + 1}
              processor={catalogue.find((entry) => entry.key === step.processorKey)}
              open={step.id === openId}
              extra={extraOf?.(step) ?? null}
              runnable={canRunThrough(steps, index)}
              run={steps.length > 1 ? run : undefined}
              onOpen={(open) => onOpen(open ? step.id : undefined)}
              onToggle={() => onToggle(step.id)}
              onRemove={() => onRemove(step.id)}
              onChange={(params) => onChange(step.id, params)}
              onCondition={(appliesTo) => onCondition(step.id, appliesTo)}
            />
          ))}
        </ol>
      </SortableContext>
    </DndContext>
  );
}
