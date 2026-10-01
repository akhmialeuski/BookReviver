import { useEffect, useRef, useState } from 'react';
import { type FitMode, type StagePage, ViewerStage } from '@/features/viewer/stage';

/**
 * Owns the OpenSeadragon stage of the viewer screen and keeps it showing the current view.
 *
 * The stage is created when the element mounts and destroyed when it leaves, which also survives React's double
 * mount in development. Every change of the view, its neighbours or the fit mode is one call of `stage.show`, and
 * the state it reports back drives the `data-state` of the canvas, which the end-to-end scenario waits on.
 */

/** Where the image of the current view is in its loading. */
export const StageState = {
  Idle: 'idle',
  Loading: 'loading',
  Ready: 'ready',
  Failed: 'failed',
} as const;

/** One state of the stage (derived from {@link StageState}). */
export type StageState = (typeof StageState)[keyof typeof StageState];

export interface ViewerStageHandle {
  /** Attach to the element the viewer fills. */
  containerRef: (element: HTMLDivElement | null) => void;
  state: StageState;
  /** Ids of the pages of the current view whose image could not be loaded. */
  failed: readonly string[];
  fit: (mode: FitMode) => void;
  zoomIn: () => void;
  zoomOut: () => void;
}

/** Keep returning the first value given for a key, so equal contents under a new object do not re-run effects. */
function useStableValue<T>(value: T, key: string): T {
  const kept = useRef({ key, value });
  if (kept.current.key !== key) {
    kept.current = { key, value };
  }
  return kept.current.value;
}

/**
 * Show a view on a stage.
 *
 * @param view Pages of the current view, left to right.
 * @param around Views to keep loaded, such as the next and the previous one.
 * @param fitMode How the view fills the viewport when it is shown.
 */
export function useViewerStage(
  view: readonly StagePage[],
  around: ReadonlyArray<readonly StagePage[]>,
  fitMode: FitMode,
): ViewerStageHandle {
  // The element appears only once the book has loaded, so it is held in state and the stage follows it
  const [element, setElement] = useState<HTMLDivElement | null>(null);
  const [stage, setStage] = useState<ViewerStage | null>(null);
  const [outcome, setOutcome] = useState<{ state: StageState; failed: readonly string[] }>({
    state: StageState.Idle,
    failed: [],
  });

  useEffect(() => {
    if (element === null) {
      return;
    }
    const created = new ViewerStage(element);
    setStage(created);
    return () => {
      created.destroy();
      setStage(null);
    };
  }, [element]);

  // The arrays are rebuilt on every render, so the effect gets them by what they hold, not by their identity: an edit
  // of a label must not make the stage show the view again and fit it, which would undo the reader's zoom
  const stableView = useStableValue(
    view,
    view.map((page) => `${page.id}=${page.infoUrl}`).join(','),
  );
  const stableAround = useStableValue(
    around,
    around.map((group) => group.map((page) => page.infoUrl).join(',')).join(';'),
  );

  useEffect(() => {
    if (stage === null) {
      return;
    }
    let current = true;
    setOutcome((previous) => ({ state: StageState.Loading, failed: previous.failed }));
    void stage.show(stableView, stableAround, fitMode).then((shown) => {
      if (current && shown !== null) {
        setOutcome({
          state: shown.failed.length > 0 ? StageState.Failed : StageState.Ready,
          failed: shown.failed,
        });
      }
    });
    return () => {
      current = false;
    };
  }, [stage, stableView, stableAround, fitMode]);

  return {
    containerRef: setElement,
    state: outcome.state,
    failed: outcome.failed,
    fit: (mode) => stage?.fit(mode),
    zoomIn: () => stage?.zoomIn(),
    zoomOut: () => stage?.zoomOut(),
  };
}
