import {
  BookOpenIcon,
  CheckIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  FileIcon,
  GitCompareIcon,
  Grid3x3Icon,
  MaximizeIcon,
  UndoIcon,
  ZoomInIcon,
  ZoomOutIcon,
} from 'lucide-react';
import { CompareMode } from '@/features/workspace/params';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu';

/**
 * The bar floating at the foot of the canvas: page turns, the number of the open page, one page or a spread, zoom,
 * and the place of the before-and-after mode.
 *
 * A stage whose steps are laid out against a grid adds the "Grid" button, which shows the grid over the page and hides it
 * again, as the key G does. The actions on the open page stand next to it: the button of what the page shows, which the
 * caller draws, and "Auto", which brings back the shape the step found while the page has one set by hand.
 *
 * The compare button is drawn and disabled for a stage that does not process pages. A stage that does gives it the mode
 * and the way to change it, and it becomes a menu of the ways to compare.
 *
 * The bar is one row high, which is the room the canvas keeps clear under a fitted page (`TOOLBAR_INSET_PX`), so the
 * caption never wraps, and in a canvas too narrow for the whole row the buttons drop their words and keep their icons.
 * The caller makes the box the bar stands in a container, which the width is read from.
 */

/** The words of a button, which only screen readers get in a narrow canvas. */
const BUTTON_WORDS = '@max-3xl:sr-only';

const COMPARE_CHOICES = [
  [CompareMode.Off, MESSAGES.processing.compare.off],
  [CompareMode.Swipe, MESSAGES.processing.compare.swipe],
  [CompareMode.Side, MESSAGES.processing.compare.side],
] as const;

export function CanvasToolbar({
  caption,
  spread,
  hasPrevious,
  hasNext,
  onPrevious,
  onNext,
  onToggleSpread,
  onFit,
  onZoomIn,
  onZoomOut,
  grid,
  contentType,
  auto,
  compare,
}: {
  /** The label and place of the open page, such as `p. 14 · 18 of 126`. */
  caption: string;
  spread: boolean;
  hasPrevious: boolean;
  hasNext: boolean;
  onPrevious: () => void;
  onNext: () => void;
  onToggleSpread: () => void;
  onFit: () => void;
  onZoomIn: () => void;
  onZoomOut: () => void;
  /** The grid over the page of a stage that has one, or nothing for a stage that has none. */
  grid?: {
    on: boolean;
    onToggle: () => void;
  };
  /** The button of what the open page shows, with its menu, or nothing for a stage that has none. */
  contentType?: React.ReactNode;
  /**
   * "Auto" for the shape of the open step on the open page, or nothing while the step has no shape set by hand there.
   */
  auto?: {
    busy: boolean;
    onClick: () => void;
  };
  /**
   * The before-and-after control of a stage that processes pages. Absent for a stage that does not, which draws the
   * button disabled.
   */
  compare?: {
    mode: CompareMode;
    onChange: (mode: CompareMode) => void;
    /** Why comparing is not possible now, or null when it is. */
    unavailable: string | null;
  };
}): React.JSX.Element {
  const labels = MESSAGES.workspace.canvas;
  return (
    <div
      role="toolbar"
      aria-label={labels.toolbar}
      className="flex items-center gap-1 rounded-lg border bg-background/95 p-1 shadow-md"
    >
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label={MESSAGES.viewer.toolbar.previous}
        title={MESSAGES.viewer.toolbar.previous}
        disabled={!hasPrevious}
        onClick={onPrevious}
      >
        <ChevronLeftIcon />
      </Button>
      <span
        className="min-w-28 px-2 text-center text-sm whitespace-nowrap tabular-nums"
        aria-live="polite"
        data-testid="canvas-caption"
      >
        {caption}
      </span>
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label={MESSAGES.viewer.toolbar.next}
        title={MESSAGES.viewer.toolbar.next}
        disabled={!hasNext}
        onClick={onNext}
      >
        <ChevronRightIcon />
      </Button>
      <span className="mx-1 h-5 w-px bg-border" aria-hidden="true" />
      <Button
        variant={spread ? 'secondary' : 'ghost'}
        size="icon-sm"
        aria-label={spread ? MESSAGES.viewer.toolbar.twoPages : MESSAGES.viewer.toolbar.onePage}
        title={spread ? MESSAGES.viewer.toolbar.twoPages : MESSAGES.viewer.toolbar.onePage}
        aria-pressed={spread}
        data-testid="canvas-spread"
        onClick={onToggleSpread}
      >
        {spread ? <BookOpenIcon /> : <FileIcon />}
      </Button>
      <span className="mx-1 h-5 w-px bg-border" aria-hidden="true" />
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label={MESSAGES.viewer.toolbar.zoomOut}
        title={MESSAGES.viewer.toolbar.zoomOut}
        onClick={onZoomOut}
      >
        <ZoomOutIcon />
      </Button>
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label={MESSAGES.viewer.toolbar.fitPage}
        title={MESSAGES.viewer.toolbar.fitPage}
        onClick={onFit}
      >
        <MaximizeIcon />
      </Button>
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label={MESSAGES.viewer.toolbar.zoomIn}
        title={MESSAGES.viewer.toolbar.zoomIn}
        onClick={onZoomIn}
      >
        <ZoomInIcon />
      </Button>
      <span className="mx-1 h-5 w-px bg-border" aria-hidden="true" />
      {grid === undefined ? null : (
        <Button
          variant={grid.on ? 'secondary' : 'ghost'}
          size="sm"
          aria-pressed={grid.on}
          title={labels.gridTitle}
          data-testid="canvas-grid-toggle"
          onClick={grid.onToggle}
        >
          <Grid3x3Icon />
          <span className={BUTTON_WORDS}>{labels.grid}</span>
        </Button>
      )}
      {contentType}
      {auto === undefined ? null : (
        <Button
          variant="ghost"
          size="sm"
          disabled={auto.busy}
          title={MESSAGES.editors.autoTitle}
          data-testid="canvas-auto"
          onClick={auto.onClick}
        >
          <UndoIcon />
          <span className={BUTTON_WORDS}>{MESSAGES.editors.auto}</span>
        </Button>
      )}
      {compare === undefined ? (
        <Button variant="ghost" size="sm" disabled title={labels.compareSoon}>
          <GitCompareIcon />
          <span className={BUTTON_WORDS}>{labels.compare}</span>
        </Button>
      ) : (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant={compare.mode === CompareMode.Off ? 'ghost' : 'secondary'}
              size="sm"
              disabled={compare.unavailable !== null}
              title={compare.unavailable ?? undefined}
              data-testid="compare-menu"
            >
              <GitCompareIcon />
              <span className={BUTTON_WORDS}>{MESSAGES.processing.compare.toggle}</span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" side="top">
            <DropdownMenuLabel>{MESSAGES.processing.compare.modeLabel}</DropdownMenuLabel>
            {COMPARE_CHOICES.map(([mode, text]) => (
              <DropdownMenuItem
                key={mode}
                data-testid={`compare-${mode}`}
                aria-checked={compare.mode === mode}
                onSelect={() => compare.onChange(mode)}
              >
                <CheckIcon className={compare.mode === mode ? 'opacity-100' : 'opacity-0'} />
                {text}
              </DropdownMenuItem>
            ))}
            <DropdownMenuSeparator />
            <p className="px-2 py-1.5 text-xs text-muted-foreground">
              {MESSAGES.processing.compare.hold}
            </p>
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </div>
  );
}
