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
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { useState } from 'react';
import type { OrderMode } from '@/api';
import type { OrderIssue } from '@/features/processing/order';
import type { Processing } from '@/features/processing/useProcessing';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';

/**
 * The drag and drop of the list of steps: the sensors for a pointer and for the keyboard, the words a screen reader hears while a step is moved, and the refusal of a place that
 * would break a required order.
 *
 * It draws no step. Each step is drawn by the list, which makes it sortable with `useSortable` under the identity the
 * step has in the draft, and is told whether a dragged step is held over it and how that place is received.
 */

const labels = MESSAGES.processing.steps;

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

export function StepSorter({
  processing,
  children,
}: {
  /** The draft of the steps, with the order it is kept in and the places that order refuses. */
  processing: Processing;
  /** Draw the steps, given how a dragged step held over each of them is received, or null when none is. */
  children: (hover: (stepId: string) => OrderMode | null) => React.ReactNode;
}): React.JSX.Element {
  const { steps, catalogue, orderMode, refusalOf, move } = processing;
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
    refusalOf(String(activeId), String(overId));
  const announcements: Announcements = {
    onDragStart: ({ active }) => labels.drag.pickedUp(nameOf(active.id)),
    onDragOver: ({ active, over }) => {
      if (over === null) {
        return undefined;
      }
      const issue = refused(active.id, over.id);
      return issue !== undefined && orderMode === 'usual'
        ? labels.drag.refused(issue.reason)
        : labels.drag.over(nameOf(over.id));
    },
    onDragEnd: ({ active, over }) =>
      over !== null && orderMode === 'usual' && refused(active.id, over.id) !== undefined
        ? labels.drag.cancelled
        : labels.drag.dropped(nameOf(active.id)),
    onDragCancel: () => labels.drag.cancelled,
  };

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
          !(orderMode === 'usual' && refused(active.id, over.id) !== undefined)
        ) {
          move(String(active.id), String(over.id));
        }
      }}
    >
      {hover === null ? null : <OrderNotice mode={orderMode} issue={hover.issue} />}
      <SortableContext items={steps.map((step) => step.id)} strategy={verticalListSortingStrategy}>
        {children((stepId) => (hover?.overId === stepId ? orderMode : null))}
      </SortableContext>
    </DndContext>
  );
}
