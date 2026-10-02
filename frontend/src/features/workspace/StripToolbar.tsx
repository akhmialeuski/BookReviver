import { LayoutGridIcon, ListIcon, TriangleAlertIcon } from 'lucide-react';
import { PageFilter } from '@/features/workspace/params';
import type { FilterCounts } from '@/features/workspace/strip';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';

/**
 * The head of the strip and of the grid: the title with how many pages are listed, the three filters with the
 * number each would list, and the switch between the strip and the grid.
 */

export function StripToolbar({
  total,
  shown,
  counts,
  filter,
  grid,
  onFilter,
  onSwitchView,
}: {
  /** The pages of the book. */
  total: number;
  /** The pages the filter lists. */
  shown: number;
  counts: FilterCounts;
  filter: PageFilter;
  /** Whether the pages are drawn as a grid, so the switch goes back to the strip. */
  grid: boolean;
  onFilter: (filter: PageFilter) => void;
  onSwitchView: () => void;
}): React.JSX.Element {
  const labels = MESSAGES.workspace.strip;
  const filterButton = (value: PageFilter, text: string): React.JSX.Element => (
    <Button
      variant={filter === value ? 'secondary' : 'ghost'}
      size="sm"
      aria-pressed={filter === value}
      data-testid={`strip-filter-${value}`}
      onClick={() => onFilter(value)}
    >
      {value === PageFilter.Check ? <TriangleAlertIcon /> : null}
      {text}
    </Button>
  );

  return (
    <div className="grid gap-2 border-b px-3 py-2">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-semibold">{labels.title}</h2>
        <span className="text-xs text-muted-foreground" data-testid="strip-count">
          {filter === PageFilter.All ? total : labels.countOf(shown, total)}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-1">
        {filterButton(PageFilter.All, labels.filters.all)}
        {filterButton(PageFilter.Check, labels.filters.check(counts[PageFilter.Check]))}
        {filterButton(PageFilter.LeftOut, labels.filters.leftOut(counts[PageFilter.LeftOut]))}
        <Button
          variant="ghost"
          size="icon-sm"
          className="ml-auto"
          aria-label={grid ? labels.list : labels.grid}
          title={grid ? labels.list : labels.grid}
          data-testid="strip-view-switch"
          onClick={onSwitchView}
        >
          {grid ? <ListIcon /> : <LayoutGridIcon />}
        </Button>
      </div>
    </div>
  );
}
