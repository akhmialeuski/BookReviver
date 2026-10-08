import { useDndContext } from '@dnd-kit/core';
import { useLayoutEffect } from 'react';

/**
 * Keeps the rectangles dnd-kit compares a held tile against in step with the tiles on the screen.
 *
 * dnd-kit measures the rectangle of every drop target when a drag starts and when the set of targets changes, and the
 * rectangle of a single target again when that target is resized. The grid is virtualized, and the virtualizer moves the
 * rows below a row to its real height once the row is measured, so tiles change place without being resized and without
 * any being added or removed: nothing tells dnd-kit, and a drop is then worked out against the places the tiles used to
 * have. The measuring strategy of the context does not help either, since `MeasuringStrategy.Always` only decides whether
 * rectangles are measured outside a drag, and a numeric `frequency` re-measures on a timer. What the context offers for
 * this is `measureDroppableContainers`, which is asked here on every mount of the component, so the grid gives it the
 * places of its rows as `key`, and it is mounted again, and measures, whenever they change.
 */

export function LiveDropRects(): null {
  const { active, measureDroppableContainers } = useDndContext();
  const held = active !== null;
  useLayoutEffect(() => {
    // An empty list of ids is every drop target, which is what the context itself asks for when a drag starts
    if (held) {
      measureDroppableContainers([]);
    }
  }, [held, measureDroppableContainers]);
  return null;
}
