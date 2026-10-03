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
  TriangleAlertIcon,
} from 'lucide-react';
import { useMemo, useState } from 'react';
import type { AppliesTo, OrderMode, ProcessorSchema } from '@/api';
import type { OrderIssue } from '@/features/processing/order';
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
 * and says how many pages already passed it. When it is given the fields the open page changes for itself, the form of a
 * step marks them.
 */

const labels = MESSAGES.processing.steps;

/** The fields of a step the open page does not change, which is what a step has when no page is open. */
const NO_PAGE_VALUES: Readonly<Record<string, unknown>> = {};

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

/** What the list needs to mark the steps that are out of their place and to keep a step from a place it cannot work. */
export interface StepOrder {
  /** The order the recipe is saved in, which decides whether a place that breaks a required order is refused. */
  mode: OrderMode;
  /** The steps that stand off the place their processors ask for, by the identity of the step. */
  issues: ReadonlyMap<string, readonly OrderIssue[]>;
  /** The required place that dropping a step on another would break, if any. */
  refusalOf: (activeId: string, overId: string) => OrderIssue | undefined;
  /** Put the steps in their usual order. */
  onRestore: () => void;
}

/** The place the dragged step is over, when it breaks a required order. */
interface Hover {
  overId: string;
  issue: OrderIssue;
}

/** The line over the list that says why the place a step is held over is not allowed, or that the free order allows it. */
export function OrderNotice({
  mode,
  issue,
}: {
  mode: OrderMode;
  issue: Pick<OrderIssue, 'reason'>;
}): React.JSX.Element {
  return (
    <p
      role="alert"
      className={cn(
        'mb-2 rounded-md border px-3 py-2 text-xs',
        mode === 'usual'
          ? 'border-destructive text-destructive'
          : 'border-status-attention text-status-attention',
      )}
      data-testid="order-refusal"
      data-mode={mode}
    >
      {mode === 'usual'
        ? labels.drag.refused(issue.reason)
        : labels.drag.allowedInFree(issue.reason)}
    </p>
  );
}

function StepCard({
  step,
  number,
  processor,
  open,
  extra,
  runnable,
  run,
  pageValues,
  issues,
  hover,
  onRestore,
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
  /** The fields the open page changes for this step, which its form marks. */
  pageValues: Readonly<Record<string, unknown>>;
  /** What is wrong with the place of this step. */
  issues: readonly OrderIssue[];
  /** How a dragged step that is over this one is received, or null when nothing is held over it. */
  hover: OrderMode | null;
  onRestore: (() => void) | undefined;
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
  const marked = useMemo(() => new Set(Object.keys(pageValues)), [pageValues]);
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
        'rounded-lg border bg-card text-card-foreground',
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
          onClick={() => onOpen(!open)}
        >
          {open ? <ChevronDownIcon className="size-4" /> : <ChevronRightIcon className="size-4" />}
          <span className="truncate">{labels.step(number, title)}</span>
        </button>
        {issues.length === 0 ? null : (
          <span
            className={cn(
              'flex shrink-0 items-center gap-1 text-xs',
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
      {issues.length === 0 || open ? null : (
        <p
          className={cn(
            'px-3 pb-1.5 text-xs',
            kind === 'required' ? 'text-destructive' : 'text-status-attention',
          )}
          data-testid="step-order-reason"
        >
          {issues[0]?.reason}
        </p>
      )}
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
          {issues.length === 0 ? null : (
            <div className="grid gap-2" data-testid="step-order-details">
              {issues.map((issue) => (
                <p
                  key={`${issue.otherId}|${issue.reason}`}
                  className={cn(
                    'text-xs',
                    issue.kind === 'required' ? 'text-destructive' : 'text-status-attention',
                  )}
                >
                  {issue.reason}
                </p>
              ))}
              {onRestore === undefined ? null : (
                <Button
                  variant="outline"
                  size="sm"
                  className="w-fit"
                  title={labels.order.restoreHint}
                  data-testid="step-restore-order"
                  onClick={onRestore}
                >
                  {labels.order.restore}
                </Button>
              )}
            </div>
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
            <ParamsForm
              processor={processor}
              params={step.params}
              marked={marked}
              onChange={onChange}
            />
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
  pageValuesOf,
  order,
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
  /** The fields the open page changes for a step, or absent when no page is open. */
  pageValuesOf?: (step: StepDraft) => Readonly<Record<string, unknown>>;
  /** The order the steps are kept in, or absent for a list that does not guard it. */
  order?: StepOrder;
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
  const [hover, setHover] = useState<Hover | null>(null);
  // A step dropped on a place that breaks a required order does not move, unless the order is free
  const refused = (activeId: string | number, overId: string | number): OrderIssue | undefined =>
    order?.refusalOf(String(activeId), String(overId));
  const announcements: Announcements = {
    onDragStart: ({ active }) => labels.drag.pickedUp(nameOf(active.id)),
    onDragOver: ({ active, over }) => {
      if (over === null) {
        return undefined;
      }
      const issue = refused(active.id, over.id);
      return issue !== undefined && order?.mode === 'usual'
        ? labels.drag.refused(issue.reason)
        : labels.drag.over(nameOf(over.id));
    },
    onDragEnd: ({ active, over }) =>
      over !== null && order?.mode === 'usual' && refused(active.id, over.id) !== undefined
        ? labels.drag.cancelled
        : labels.drag.dropped(nameOf(active.id)),
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
      onDragOver={({ active, over }) => {
        const issue = over === null ? undefined : refused(active.id, over.id);
        setHover(over === null || issue === undefined ? null : { overId: String(over.id), issue });
      }}
      onDragCancel={() => setHover(null)}
      onDragEnd={({ active, over }) => {
        setHover(null);
        if (
          over !== null &&
          !(order?.mode === 'usual' && refused(active.id, over.id) !== undefined)
        ) {
          onMove(String(active.id), String(over.id));
        }
      }}
    >
      {hover === null || order === undefined ? null : (
        <OrderNotice mode={order.mode} issue={hover.issue} />
      )}
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
              pageValues={pageValuesOf?.(step) ?? NO_PAGE_VALUES}
              issues={order?.issues.get(step.id) ?? []}
              hover={hover?.overId === step.id ? (order?.mode ?? null) : null}
              onRestore={order?.onRestore}
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
