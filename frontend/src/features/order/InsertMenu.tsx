import type { InsertSpec } from '@/features/order/insert';
import { MESSAGES } from '@/shared/messages';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu';

/**
 * The menu that adds a blank leaf or a missing page next to the selected pages, or at the end of the book.
 *
 * The toolbar and the panel of selected pages both open it, each with its own button, which the caller passes as the
 * trigger. The entries that are measured from the selection are off while nothing is selected.
 */

export function InsertMenu({
  hasSelection,
  onInsert,
  children,
}: {
  hasSelection: boolean;
  onInsert: (spec: InsertSpec) => void;
  /** The button that opens the menu. */
  children: React.ReactNode;
}): React.JSX.Element {
  const text = MESSAGES.order.insert;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>{children}</DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuItem
          disabled={!hasSelection}
          onSelect={() => onInsert({ origin: 'blank', place: 'before' })}
        >
          {text.blankBefore}
        </DropdownMenuItem>
        <DropdownMenuItem
          disabled={!hasSelection}
          onSelect={() => onInsert({ origin: 'blank', place: 'after' })}
        >
          {text.blankAfter}
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => onInsert({ origin: 'blank', place: 'end' })}>
          {text.blankEnd}
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          disabled={!hasSelection}
          onSelect={() => onInsert({ origin: 'placeholder', place: 'before' })}
        >
          {text.missingBefore}
        </DropdownMenuItem>
        <DropdownMenuItem
          disabled={!hasSelection}
          onSelect={() => onInsert({ origin: 'placeholder', place: 'after' })}
        >
          {text.missingAfter}
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => onInsert({ origin: 'placeholder', place: 'end' })}>
          {text.missingEnd}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
