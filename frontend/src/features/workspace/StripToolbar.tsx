import { LayoutGridIcon, ListIcon, TriangleAlertIcon } from 'lucide-react';
import type { RecipeKind, StepFlag } from '@/api';
import { PageFilter } from '@/features/workspace/params';
import type { PageListFilters } from '@/features/workspace/strip';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';

/**
 * The head of the strip and of the grid: the title with how many pages are listed, the filters with the
 * number each would list, and the switch between the strip and the grid.
 */

export function StripToolbar({
  total,
  shown,
  counts,
  filter,
  grid,
  withWide = false,
  kinds,
  stopped,
  flagged,
  onFilter,
  onSwitchView,
}: PageListFilters & {
  /** The pages the filter lists. */
  shown: number;
  /** Whether the pages are drawn as a grid, so the switch goes back to the strip. */
  grid: boolean;
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
        {filterButton(PageFilter.Bad, labels.filters.bad(counts[PageFilter.Bad]))}
        {filterButton(PageFilter.LeftOut, labels.filters.leftOut(counts[PageFilter.LeftOut]))}
        {withWide
          ? filterButton(PageFilter.Wide, labels.filters.wide(counts[PageFilter.Wide]))
          : null}
        {kinds === undefined || kinds.options.length === 0 ? null : (
          <select
            aria-label={labels.kind.label}
            data-testid="strip-kind-filter"
            className="h-8 max-w-40 min-w-0 rounded-md border border-input bg-background px-2 text-xs shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
            value={kinds.selected ?? ''}
            onChange={(event) =>
              kinds.onSelect(event.target.value === '' ? null : (event.target.value as RecipeKind))
            }
          >
            <option value="">{labels.kind.all}</option>
            {kinds.options.map((option) => (
              <option key={option.kind} value={option.kind}>
                {labels.kind.option(MESSAGES.processing.recipe.kinds[option.kind], option.pages)}
              </option>
            ))}
          </select>
        )}
        {stopped === undefined || stopped.options.length === 0 ? null : (
          <select
            aria-label={labels.stopped.label}
            data-testid="strip-stopped-filter"
            className="h-8 max-w-44 min-w-0 rounded-md border border-input bg-background px-2 text-xs shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
            value={stopped.selected === null ? '' : String(stopped.selected)}
            onChange={(event) =>
              stopped.onSelect(event.target.value === '' ? null : Number(event.target.value))
            }
          >
            <option value="">{labels.stopped.all}</option>
            {stopped.options.map((option) => (
              <option key={option.step} value={option.step}>
                {labels.stopped.option(stopped.titleOf(option.step), option.pages)}
              </option>
            ))}
          </select>
        )}
        {flagged === undefined ? null : (
          <select
            aria-label={labels.flag.label}
            data-testid="strip-step-filter"
            className="h-8 max-w-44 min-w-0 rounded-md border border-input bg-background px-2 text-xs shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
            value={flagged.selected ?? ''}
            onChange={(event) =>
              flagged.onSelect(event.target.value === '' ? null : (event.target.value as StepFlag))
            }
          >
            <option value="">{labels.flag.all}</option>
            {flagged.options.map((option) => (
              <option key={option.flag} value={option.flag}>
                {labels.flag.option(option.flag, option.pages)}
              </option>
            ))}
          </select>
        )}
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
