import { DropdownMenu as DropdownMenuPrimitive } from 'radix-ui';
import type * as React from 'react';
import { useRef } from 'react';
import { cn } from '@/shared/lib/utils';

/**
 * The shadcn/ui dropdown menu over Radix: a list of actions that opens under a button.
 */

function DropdownMenu(
  props: React.ComponentProps<typeof DropdownMenuPrimitive.Root>,
): React.JSX.Element {
  return <DropdownMenuPrimitive.Root {...props} />;
}

function DropdownMenuTrigger(
  props: React.ComponentProps<typeof DropdownMenuPrimitive.Trigger>,
): React.JSX.Element {
  return <DropdownMenuPrimitive.Trigger {...props} />;
}

function DropdownMenuContent({
  className,
  align = 'end',
  sideOffset = 6,
  onPointerDownOutside,
  ref,
  ...props
}: React.ComponentProps<typeof DropdownMenuPrimitive.Content>): React.JSX.Element {
  const content = useRef<HTMLDivElement | null>(null);
  return (
    <DropdownMenuPrimitive.Portal>
      <DropdownMenuPrimitive.Content
        ref={(node) => {
          content.current = node;
          if (typeof ref === 'function') {
            ref(node);
          } else if (ref) {
            ref.current = node;
          }
        }}
        align={align}
        sideOffset={sideOffset}
        onPointerDownOutside={(event) => {
          onPointerDownOutside?.(event);
          // The list of a menu that has just closed stays mounted while it fades out and still dismisses on a press
          // outside, so a press on its own trigger, which opens the menu again, would close it in the same breath.
          // The trigger of another menu still dismisses it.
          const trigger =
            event.target instanceof Element ? event.target.closest('[aria-haspopup="menu"]') : null;
          if (trigger !== null && trigger.id === content.current?.getAttribute('aria-labelledby')) {
            event.preventDefault();
          }
        }}
        className={cn(
          'z-50 min-w-48 overflow-hidden rounded-md border bg-popover p-1 text-popover-foreground shadow-md data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95',
          className,
        )}
        {...props}
      />
    </DropdownMenuPrimitive.Portal>
  );
}

function DropdownMenuItem({
  className,
  ...props
}: React.ComponentProps<typeof DropdownMenuPrimitive.Item>): React.JSX.Element {
  return (
    <DropdownMenuPrimitive.Item
      className={cn(
        "relative flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-hidden select-none focus:bg-accent focus:text-accent-foreground data-[disabled]:pointer-events-none data-[disabled]:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
        className,
      )}
      {...props}
    />
  );
}

function DropdownMenuLabel({
  className,
  ...props
}: React.ComponentProps<typeof DropdownMenuPrimitive.Label>): React.JSX.Element {
  return (
    <DropdownMenuPrimitive.Label
      className={cn('px-2 py-1.5 text-sm font-medium', className)}
      {...props}
    />
  );
}

function DropdownMenuSeparator({
  className,
  ...props
}: React.ComponentProps<typeof DropdownMenuPrimitive.Separator>): React.JSX.Element {
  return (
    <DropdownMenuPrimitive.Separator
      className={cn('-mx-1 my-1 h-px bg-border', className)}
      {...props}
    />
  );
}

export {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
};
