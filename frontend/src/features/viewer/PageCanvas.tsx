import { type Ref, useImperativeHandle } from 'react';
import type { FitMode, StagePage } from '@/features/viewer/stage';
import { StageState, useViewerStage } from '@/features/viewer/useViewerStage';
import { MESSAGES } from '@/shared/messages';

/**
 * The canvas that draws pages, shared by the reading mode and the workspace of a stage.
 *
 * It takes the pages to show and the views to keep loaded next to them, and draws them on one OpenSeadragon stage.
 * What the pages are, their images and their order are the caller's, so the viewer passes the images of the book
 * and a stage passes the images of its own result. The caller moves the view through the handle, which holds the
 * fit and the zoom, and reads the loading state from the `data-state` of the element. The `data-sources` of the element
 * lists the pyramids the view is drawn from.
 */

/** The controls a screen drives the canvas with. */
export interface PageCanvasHandle {
  fit: (mode: FitMode) => void;
  zoomIn: () => void;
  zoomOut: () => void;
}

export function PageCanvas({
  view,
  around,
  fitMode,
  handle,
}: {
  /** Pages of the current view, left to right. */
  view: readonly StagePage[];
  /** Views to keep loaded and hidden, such as the next and the previous one. */
  around: ReadonlyArray<readonly StagePage[]>;
  fitMode: FitMode;
  handle?: Ref<PageCanvasHandle>;
}): React.JSX.Element {
  const stage = useViewerStage(view, around, fitMode);
  useImperativeHandle(handle, () => ({
    fit: stage.fit,
    zoomIn: stage.zoomIn,
    zoomOut: stage.zoomOut,
  }));

  const missingImage = view.some((page) => page.infoUrl === null);

  return (
    <div className="relative size-full min-w-0">
      <div
        ref={stage.containerRef}
        className="absolute inset-0 bg-muted"
        data-testid="viewer-canvas"
        data-state={stage.state}
        data-page-ids={view.map((page) => page.id).join(',')}
        data-sources={view
          .flatMap((page) => (page.infoUrl === null ? [] : [page.infoUrl]))
          .join(' ')}
      />
      {missingImage || stage.state === StageState.Failed ? (
        <p className="absolute inset-x-0 top-3 mx-auto w-fit rounded-md bg-background/90 px-3 py-1 text-sm shadow">
          {stage.state === StageState.Failed ? MESSAGES.viewer.loadFailed : MESSAGES.viewer.noImage}
        </p>
      ) : null}
    </div>
  );
}
