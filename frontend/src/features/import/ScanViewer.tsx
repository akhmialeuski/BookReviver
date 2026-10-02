import {
  ChevronLeftIcon,
  ChevronRightIcon,
  MaximizeIcon,
  ZoomInIcon,
  ZoomOutIcon,
} from 'lucide-react';
import { useRef } from 'react';
import type { ScanSchema } from '@/api';
import { PageCanvas, type PageCanvasHandle } from '@/features/viewer/PageCanvas';
import { FitMode, type StagePage } from '@/features/viewer/stage';
import { useViewerKeys } from '@/features/viewer/useViewerKeys';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';

/**
 * One scan large on the canvas, with buttons and keys to turn to the scan before and after it in the grid, and to
 * zoom.
 *
 * The scans it turns between are those of the page of the grid that is loaded, and the scans next to the open one are
 * kept loaded hidden, so a turn is quick. The canvas is the one the reading mode and the other stages draw with.
 */

function viewOf(scan: ScanSchema | undefined): StagePage[] {
  return scan === undefined ? [] : [{ id: scan.id, infoUrl: scan.images?.iiif_info ?? null }];
}

export function ScanViewer({
  scans,
  scan,
  total,
  onOpen,
}: {
  /** The scans of the loaded page of the grid, in the order of the file. */
  scans: readonly ScanSchema[];
  /** The scan that is open, one of `scans`. */
  scan: ScanSchema;
  /** The scans in the file, which the page of the grid is only a part of. */
  total: number;
  onOpen: (scanId: string) => void;
}): React.JSX.Element {
  const canvas = useRef<PageCanvasHandle>(null);
  const index = scans.findIndex((entry) => entry.id === scan.id);
  const previous = scans[index - 1];
  const next = scans[index + 1];
  const toolbar = MESSAGES.viewer.toolbar;

  const open = (target: ScanSchema | undefined): void => {
    if (target !== undefined) {
      onOpen(target.id);
    }
  };
  useViewerKeys({
    previous: () => open(previous),
    next: () => open(next),
    first: () => open(scans[0]),
    last: () => open(scans.at(-1)),
  });

  return (
    <div className="relative size-full" data-testid="scan-viewer">
      <PageCanvas
        view={viewOf(scan)}
        around={[viewOf(next), viewOf(previous)].filter((view) => view.length > 0)}
        fitMode={FitMode.Page}
        handle={canvas}
      />
      <div className="pointer-events-none absolute inset-x-0 bottom-4 flex justify-center">
        <div
          role="toolbar"
          aria-label={MESSAGES.import.viewer.toolbar}
          className="pointer-events-auto flex items-center gap-1 rounded-lg border bg-background/95 p-1 shadow-md"
        >
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={toolbar.previous}
            title={toolbar.previous}
            disabled={previous === undefined}
            onClick={() => open(previous)}
          >
            <ChevronLeftIcon />
          </Button>
          <span
            className="min-w-28 px-2 text-center text-sm tabular-nums"
            aria-live="polite"
            data-testid="scan-caption"
          >
            {MESSAGES.import.viewer.caption(scan.number + 1, total)}
          </span>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={toolbar.next}
            title={toolbar.next}
            disabled={next === undefined}
            onClick={() => open(next)}
          >
            <ChevronRightIcon />
          </Button>
          <span className="mx-1 h-5 w-px bg-border" aria-hidden="true" />
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={toolbar.zoomOut}
            title={toolbar.zoomOut}
            onClick={() => canvas.current?.zoomOut()}
          >
            <ZoomOutIcon />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={toolbar.fitPage}
            title={toolbar.fitPage}
            onClick={() => canvas.current?.fit(FitMode.Page)}
          >
            <MaximizeIcon />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={toolbar.zoomIn}
            title={toolbar.zoomIn}
            onClick={() => canvas.current?.zoomIn()}
          >
            <ZoomInIcon />
          </Button>
        </div>
      </div>
    </div>
  );
}
