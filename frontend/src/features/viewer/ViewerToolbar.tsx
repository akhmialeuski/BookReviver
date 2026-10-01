import {
  BookOpenIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  ChevronsLeftIcon,
  ChevronsRightIcon,
  FileIcon,
  MaximizeIcon,
  MoveHorizontalIcon,
  PanelRightCloseIcon,
  PanelRightOpenIcon,
  ZoomInIcon,
  ZoomOutIcon,
} from 'lucide-react';
import { useId, useState } from 'react';
import { FitMode } from '@/features/viewer/stage';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { Input } from '@/shared/ui/input';

/**
 * The controls above the viewer: page turns, the slider, the go-to field, zoom, the spread switch and the panel.
 *
 * The slider keeps its value in a draft while it is dragged, and opens the page when the reader lets go, so a drag
 * across a long book does not load every page it passes. The go-to field counts from 1, as the book is read.
 */

export interface ToolbarProps {
  /** Number of pages in the book. */
  count: number;
  /** Index of the first page of the current view. */
  index: number;
  spread: boolean;
  fitMode: FitMode;
  panelOpen: boolean;
  hasPrevious: boolean;
  hasNext: boolean;
  onOpenIndex: (index: number) => void;
  onPrevious: () => void;
  onNext: () => void;
  onFitMode: (mode: FitMode) => void;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onToggleSpread: () => void;
  onTogglePanel: () => void;
}

export function ViewerToolbar(props: ToolbarProps): React.JSX.Element {
  const labels = MESSAGES.viewer.toolbar;
  const goToId = useId();
  const [draft, setDraft] = useState<number | null>(null);
  const [goTo, setGoTo] = useState('');
  const lastIndex = Math.max(props.count - 1, 0);

  const commitDraft = (): void => {
    if (draft !== null) {
      props.onOpenIndex(draft);
      setDraft(null);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-2 border-b px-3 py-2" role="toolbar">
      <Button
        variant="outline"
        size="icon-sm"
        aria-label={labels.first}
        title={labels.first}
        disabled={!props.hasPrevious}
        onClick={() => props.onOpenIndex(0)}
      >
        <ChevronsLeftIcon />
      </Button>
      <Button
        variant="outline"
        size="icon-sm"
        aria-label={labels.previous}
        title={labels.previous}
        disabled={!props.hasPrevious}
        onClick={props.onPrevious}
      >
        <ChevronLeftIcon />
      </Button>
      <Button
        variant="outline"
        size="icon-sm"
        aria-label={labels.next}
        title={labels.next}
        disabled={!props.hasNext}
        onClick={props.onNext}
      >
        <ChevronRightIcon />
      </Button>
      <Button
        variant="outline"
        size="icon-sm"
        aria-label={labels.last}
        title={labels.last}
        disabled={!props.hasNext}
        onClick={() => props.onOpenIndex(lastIndex)}
      >
        <ChevronsRightIcon />
      </Button>

      <input
        type="range"
        aria-label={labels.slider}
        min={0}
        max={lastIndex}
        step={1}
        value={draft ?? props.index}
        disabled={props.count < 2}
        onChange={(event) => setDraft(Number(event.target.value))}
        onPointerUp={commitDraft}
        onKeyUp={commitDraft}
        onBlur={commitDraft}
        className="h-2 min-w-24 flex-1 accent-primary"
        data-testid="page-slider"
      />

      <form
        className="flex items-center gap-1"
        onSubmit={(event) => {
          event.preventDefault();
          const position = Number.parseInt(goTo, 10);
          if (Number.isInteger(position)) {
            props.onOpenIndex(Math.min(Math.max(position, 1), props.count) - 1);
            setGoTo('');
          }
        }}
      >
        <label htmlFor={goToId} className="sr-only">
          {labels.goTo}
        </label>
        <Input
          id={goToId}
          type="number"
          inputMode="numeric"
          min={1}
          max={props.count}
          value={goTo}
          placeholder={String(props.index + 1)}
          onChange={(event) => setGoTo(event.target.value)}
          className="h-8 w-20"
        />
        <span className="text-sm text-muted-foreground">{labels.goToOf(props.count)}</span>
        <Button type="submit" variant="outline" size="sm" disabled={goTo.trim() === ''}>
          {labels.goToSubmit}
        </Button>
      </form>

      <Button
        variant={props.fitMode === FitMode.Page ? 'secondary' : 'outline'}
        size="icon-sm"
        aria-label={labels.fitPage}
        title={labels.fitPage}
        aria-pressed={props.fitMode === FitMode.Page}
        onClick={() => props.onFitMode(FitMode.Page)}
      >
        <MaximizeIcon />
      </Button>
      <Button
        variant={props.fitMode === FitMode.Width ? 'secondary' : 'outline'}
        size="icon-sm"
        aria-label={labels.fitWidth}
        title={labels.fitWidth}
        aria-pressed={props.fitMode === FitMode.Width}
        onClick={() => props.onFitMode(FitMode.Width)}
      >
        <MoveHorizontalIcon />
      </Button>
      <Button
        variant="outline"
        size="icon-sm"
        aria-label={labels.zoomIn}
        title={labels.zoomIn}
        onClick={props.onZoomIn}
      >
        <ZoomInIcon />
      </Button>
      <Button
        variant="outline"
        size="icon-sm"
        aria-label={labels.zoomOut}
        title={labels.zoomOut}
        onClick={props.onZoomOut}
      >
        <ZoomOutIcon />
      </Button>

      <Button
        variant={props.spread ? 'secondary' : 'outline'}
        size="sm"
        aria-pressed={props.spread}
        onClick={props.onToggleSpread}
      >
        {props.spread ? <BookOpenIcon /> : <FileIcon />}
        {props.spread ? labels.twoPages : labels.onePage}
      </Button>
      <Button
        variant="outline"
        size="icon-sm"
        aria-label={props.panelOpen ? labels.hidePanel : labels.showPanel}
        title={props.panelOpen ? labels.hidePanel : labels.showPanel}
        aria-pressed={props.panelOpen}
        onClick={props.onTogglePanel}
      >
        {props.panelOpen ? <PanelRightCloseIcon /> : <PanelRightOpenIcon />}
      </Button>
    </div>
  );
}
