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
import { ChevronDownIcon, ChevronRightIcon, GripVerticalIcon, Trash2Icon } from 'lucide-react';
import type { ProcessorSchema } from '@/api';
import { ParamsForm } from '@/features/processing/ParamsForm';
import type { StepDraft } from '@/features/processing/recipe';
import { fitsSchema, formSchemaOf } from '@/features/processing/schema';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { Switch } from '@/shared/ui/switch';

/**
 * The steps of the recipe as a list the reader reorders by dragging a handle or with the keyboard, switches on and off,
 * removes, and opens to change their settings.
 *
 * A step shows the title of its processor from the catalogue. A step whose processor is not installed on this machine
 * still shows, with a note, so a recipe is never edited blind and the step can still be removed.
 */

const labels = MESSAGES.processing.steps;

function StepCard({
  step,
  number,
  processor,
  open,
  extra,
  onOpen,
  onToggle,
  onRemove,
  onChange,
}: {
  step: StepDraft;
  /** Its place in the recipe, from one. */
  number: number;
  processor: ProcessorSchema | undefined;
  open: boolean;
  /** What the step shows under its settings when it is open, such as the button that measures the book. */
  extra: React.ReactNode;
  onOpen: (open: boolean) => void;
  onToggle: () => void;
  onRemove: () => void;
  onChange: (params: Record<string, unknown>) => void;
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
      {open ? (
        <div className="grid gap-2 border-t px-3 py-3">
          {step.enabled ? null : (
            <p className="text-xs text-muted-foreground">{labels.switchedOff}</p>
          )}
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
  onOpen,
  onMove,
  onToggle,
  onRemove,
  onChange,
}: {
  steps: readonly StepDraft[];
  catalogue: readonly ProcessorSchema[];
  openId: string | undefined;
  /** What a step shows under its settings when it is open, or nothing. */
  extraOf?: (step: StepDraft) => React.ReactNode;
  onOpen: (id: string | undefined) => void;
  onMove: (activeId: string, overId: string) => void;
  onToggle: (id: string) => void;
  onRemove: (id: string) => void;
  onChange: (id: string, params: Record<string, unknown>) => void;
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
              onOpen={(open) => onOpen(open ? step.id : undefined)}
              onToggle={() => onToggle(step.id)}
              onRemove={() => onRemove(step.id)}
              onChange={(params) => onChange(step.id, params)}
            />
          ))}
        </ol>
      </SortableContext>
    </DndContext>
  );
}
