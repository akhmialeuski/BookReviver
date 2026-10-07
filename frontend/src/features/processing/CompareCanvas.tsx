import { LoaderCircleIcon } from 'lucide-react';
import {
  type ReactNode,
  type Ref,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from 'react';
import type { EditorScene } from '@/features/editors/scene';
import { usePlaceWriter } from '@/features/place/PlaceWriterContext';
import { Restore } from '@/features/place/writer';
import type { ComparePair, PairPlacement } from '@/features/processing/compare';
import { clampDivider, DIVIDER_CENTRE } from '@/features/processing/compare';
import { CompareStage, type EditorReach } from '@/features/processing/compareStage';
import { useHoldKey } from '@/features/processing/useHoldKey';
import type { PageCanvasHandle } from '@/features/viewer/PageCanvas';
import { CompareMode } from '@/features/workspace/params';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';

/**
 * The canvas of a processing stage: the page after the stage, with the page before it one swipe, one key or one split
 * away.
 *
 * It owns the OpenSeadragon stage, which loads the two pictures once and changes only what is shown when the mode
 * changes. The swipe has a divider the reader drags, or moves with the arrow keys once it has the focus, and holding
 * Space shows the picture before in every mode. The `data-state` of the canvas says where the loading stands, which the
 * end-to-end scenarios wait on, as they do for the canvas of the reading mode, and its `data-sources` lists the addresses
 * of the pictures the stage has loaded, so a scenario can tell which version is drawn.
 *
 * A page editor is drawn over the canvas by the `overlay` the screen passes in. It gets the viewer and the one picture
 * the page is on, once that picture is loaded, and the page is fitted with room round it for the editor's labels.
 */

const labels = MESSAGES.processing.compare;
const KEY_STEP = 0.05;

type LoadState = 'idle' | 'loading' | 'ready' | 'failed';

export function CompareCanvas({
  pairs,
  mode,
  beforeLabel,
  afterLabel,
  notice,
  pageIds,
  handle,
  overlay,
  roomShare = 0,
  reach = null,
  placement = null,
}: {
  pairs: ComparePair;
  mode: CompareMode;
  /** What stands under the picture before, such as `Before · result of Order`. */
  beforeLabel: string;
  afterLabel: string;
  /**
   * What the preview is doing or why it failed, or null for nothing to say. While a preview is being made, or waits for
   * another job, the picture after is not the newest.
   */
  notice: { text: string; working: boolean } | null;
  pageIds: readonly string[];
  handle?: Ref<PageCanvasHandle>;
  /** Draws a page editor over the canvas, or is left out when no editor is open. */
  overlay?: (scene: EditorScene) => ReactNode;
  /** The room to leave round the page when it is fitted, as a share of its height. */
  roomShare?: number;
  /** What the open editor draws beyond the page, which the fit holds too, or null for nothing. */
  reach?: EditorReach | null;
  /** Where the two pictures stand in one world, or null for both as tall as a page. */
  placement?: PairPlacement | null;
}): React.JSX.Element {
  const [first, setFirst] = useState<HTMLDivElement | null>(null);
  const [aside, setAside] = useState<HTMLDivElement | null>(null);
  const [stage, setStage] = useState<CompareStage | null>(null);
  const [state, setState] = useState<LoadState>('idle');
  const [sources, setSources] = useState<readonly string[]>([]);
  const [divider, setDivider] = useState(DIVIDER_CENTRE);
  const [holding, setHolding] = useState(false);
  const frame = useRef<HTMLDivElement>(null);
  const hasBefore = pairs.before !== null;
  // The screen builds a new pair on every render, so the effects follow what the pair holds
  const beforeKind = pairs.before?.kind ?? null;
  const beforeUrl = pairs.before?.url ?? null;
  const afterKind = pairs.after?.kind ?? null;
  const afterUrl = pairs.after?.url ?? null;
  const pageKey = pageIds.join(',');
  // The screen works the place out on every render, so the effect follows the numbers it holds
  const placeBase = placement?.base ?? null;
  const placeLeft = placement?.left ?? 0;
  const placeTop = placement?.top ?? 0;
  const placeWidth = placement?.width ?? 0;
  const placeHeight = placement?.height ?? 0;
  const stand = useMemo<PairPlacement | null>(
    () =>
      placeBase === null
        ? null
        : {
            base: placeBase,
            left: placeLeft,
            top: placeTop,
            width: placeWidth,
            height: placeHeight,
          },
    [placeBase, placeLeft, placeTop, placeWidth, placeHeight],
  );

  useHoldKey(hasBefore, setHolding);
  const place = usePlaceWriter();

  useEffect(() => {
    if (first === null || aside === null) {
      return;
    }
    const created = new CompareStage(first, aside, {
      restore: () => place?.takeRestore(Restore.Canvas) ?? null,
      onViewChange: () => place?.touch(),
    });
    const detach = place?.attachCanvas(() => created.readView());
    setStage(created);
    return () => {
      detach?.();
      created.destroy();
      setStage(null);
    };
  }, [first, aside, place]);

  // A picture is loaded once for the page, whatever the mode is, so the mode is set apart from the pictures
  useEffect(() => {
    if (stage === null) {
      return;
    }
    let current = true;
    setState('loading');
    setSources([]);
    const before =
      beforeUrl === null || beforeKind === null ? null : { kind: beforeKind, url: beforeUrl };
    const after =
      afterUrl === null || afterKind === null ? null : { kind: afterKind, url: afterUrl };
    void stage.show(before, after, pageKey, stand).then((shown) => {
      if (current && shown !== null) {
        setState(shown.failed.length > 0 ? 'failed' : 'ready');
        setSources(shown.loaded);
      }
    });
    return () => {
      current = false;
    };
  }, [stage, beforeKind, beforeUrl, afterKind, afterUrl, pageKey, stand]);

  useEffect(() => stage?.setMode(mode), [stage, mode]);
  useEffect(() => stage?.setDivider(divider), [stage, divider]);
  useEffect(() => stage?.setHolding(holding), [stage, holding]);
  useEffect(() => stage?.setPadding(roomShare), [stage, roomShare]);
  // The screen works the rectangle out on every render, so the effect follows the numbers it holds
  const reachKey = JSON.stringify(reach);
  useEffect(() => stage?.setReach(JSON.parse(reachKey) as EditorReach | null), [stage, reachKey]);

  useImperativeHandle(handle, () => ({
    fit: () => stage?.fit(),
    zoomIn: () => stage?.zoomIn(),
    zoomOut: () => stage?.zoomOut(),
  }));

  const side = mode === CompareMode.Side;
  const swipe = mode === CompareMode.Swipe && hasBefore && pairs.after !== null && !holding;
  const move = (clientX: number): void => {
    const box = frame.current?.getBoundingClientRect();
    if (box !== undefined && box.width > 0) {
      setDivider(clampDivider((clientX - box.left) / box.width));
    }
  };

  return (
    <div
      ref={frame}
      className="relative size-full min-w-0 overflow-hidden"
      // A button that keeps the focus would take Space for itself, so a click on the picture lets go of it
      onPointerDown={() => {
        if (document.activeElement instanceof HTMLElement) {
          document.activeElement.blur();
        }
      }}
    >
      <div className="absolute inset-0 flex bg-muted">
        <div
          ref={setFirst}
          className={cn('h-full', side ? 'w-1/2' : 'w-full')}
          data-testid="viewer-canvas"
          data-state={state}
          data-sources={sources.join(' ')}
          data-mode={mode}
          data-holding={holding}
          data-page-ids={pageIds.join(',')}
        />
        <div
          ref={setAside}
          className={cn('h-full', side ? 'w-1/2 border-l' : 'hidden')}
          data-testid="viewer-canvas-after"
        />
      </div>

      {overlay === undefined || stage === null || state !== 'ready' || stage.image === null
        ? null
        : overlay({ viewer: stage.viewer, image: stage.image })}

      {swipe ? (
        <div
          className="pointer-events-none absolute inset-y-0 w-0"
          style={{ left: `${divider * 100}%` }}
        >
          <div className="absolute inset-y-0 -left-px w-0.5 bg-white shadow" />
          <div
            role="slider"
            tabIndex={0}
            aria-label={labels.handle}
            aria-orientation="horizontal"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(divider * 100)}
            data-testid="compare-handle"
            className="pointer-events-auto absolute top-1/2 -left-4 flex size-8 -translate-y-1/2 cursor-ew-resize touch-none items-center justify-center rounded-full border bg-background text-xs shadow-md outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
            onPointerDown={(event) => event.currentTarget.setPointerCapture(event.pointerId)}
            onPointerMove={(event) => {
              if (event.currentTarget.hasPointerCapture(event.pointerId)) {
                move(event.clientX);
              }
            }}
            onKeyDown={(event) => {
              if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
                event.preventDefault();
                setDivider((now) =>
                  clampDivider(now + (event.key === 'ArrowLeft' ? -KEY_STEP : KEY_STEP)),
                );
              }
            }}
          >
            ⇆
          </div>
        </div>
      ) : null}

      {hasBefore && (swipe || side || holding) ? (
        <p className="pointer-events-none absolute bottom-16 left-4 rounded-full bg-black/70 px-3 py-1 text-xs text-white">
          {beforeLabel}
        </p>
      ) : null}
      {pairs.after !== null && (swipe || side) ? (
        <p className="pointer-events-none absolute right-4 bottom-16 rounded-full bg-black/70 px-3 py-1 text-xs text-white">
          {afterLabel}
        </p>
      ) : null}
      {notice === null ? null : (
        <p
          role="status"
          className={cn(
            'absolute top-3 right-3 flex max-w-80 items-center gap-2 rounded-md bg-background/90 px-3 py-1 text-sm shadow',
            notice.working ? '' : 'text-destructive',
          )}
          data-testid={notice.working ? 'preview-working' : 'preview-error'}
        >
          {notice.working ? (
            <LoaderCircleIcon className="size-4 shrink-0 animate-spin" aria-hidden="true" />
          ) : null}
          {notice.text}
        </p>
      )}
      {state === 'failed' || (pairs.before === null && pairs.after === null) ? (
        <p className="absolute inset-x-0 top-3 mx-auto w-fit rounded-md bg-background/90 px-3 py-1 text-sm shadow">
          {state === 'failed' ? MESSAGES.viewer.loadFailed : MESSAGES.viewer.noImage}
        </p>
      ) : null}
    </div>
  );
}
