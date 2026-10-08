import type { PageListFilters } from '@/features/workspace/strip';
import { MESSAGES } from '@/shared/messages';

/** The words in place of the pages when the filter lists none, for the strip and the grid alike. */
export function StripEmpty({
  filter,
  flagged,
}: Pick<PageListFilters, 'filter' | 'flagged'>): React.JSX.Element {
  return (
    <p className="p-4 text-sm text-muted-foreground">
      {flagged?.selected == null
        ? MESSAGES.workspace.strip.empty[filter]
        : MESSAGES.workspace.strip.flag.empty}
    </p>
  );
}
