import {
  BookOpenIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  FileIcon,
  GitCompareIcon,
  MaximizeIcon,
  ZoomInIcon,
  ZoomOutIcon,
} from 'lucide-react';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';

/**
 * The bar floating at the foot of the canvas: page turns, the number of the open page, one page or a spread, zoom,
 * and the place of the before-and-after mode.
 *
 * The compare button is drawn and disabled. The processing stages turn it on.
 */

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
        className="min-w-28 px-2 text-center text-sm tabular-nums"
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
      <Button variant="ghost" size="sm" disabled title={labels.compareSoon}>
        <GitCompareIcon />
        {labels.compare}
      </Button>
    </div>
  );
}
