import { Collapsible as CollapsiblePrimitive } from 'radix-ui';
import type * as React from 'react';

/**
 * The shadcn/ui collapsible over Radix: a region that a trigger opens and closes.
 */

function Collapsible(
  props: React.ComponentProps<typeof CollapsiblePrimitive.Root>,
): React.JSX.Element {
  return <CollapsiblePrimitive.Root {...props} />;
}

function CollapsibleTrigger(
  props: React.ComponentProps<typeof CollapsiblePrimitive.Trigger>,
): React.JSX.Element {
  return <CollapsiblePrimitive.Trigger {...props} />;
}

function CollapsibleContent(
  props: React.ComponentProps<typeof CollapsiblePrimitive.Content>,
): React.JSX.Element {
  return <CollapsiblePrimitive.Content {...props} />;
}

export { Collapsible, CollapsibleContent, CollapsibleTrigger };
