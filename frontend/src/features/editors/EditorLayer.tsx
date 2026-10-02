import type Konva from 'konva';
import OpenSeadragon from 'openseadragon';
import { type ReactNode, useEffect, useRef } from 'react';
import { Layer, Stage } from 'react-konva';
import type { EditorScene, SceneFrame } from '@/features/editors/scene';

/**
 * The react-konva stage laid over the canvas of OpenSeadragon, which every editor draws its shapes on.
 *
 * The stage covers the viewer, so it takes the pointer from it. The layer gives back what the viewer would have done:
 * the wheel zooms, and a drag on the empty page pans, both by the viewport of the viewer, so the picture and the shapes
 * move together. A drag that starts on a shape belongs to the shape. The element takes the focus on a click and keeps
 * its own arrow keys, which the viewer would use to turn pages, and passes the key presses and the wheel with `Alt` to
 * the editor.
 */

/** How much one notch of the wheel zooms, the same as the viewer's own. */
const WHEEL_ZOOM = 1.2;

export function EditorLayer({
  scene,
  frame,
  label,
  value,
  data,
  onKeyDown,
  onAltWheel,
  overlay,
  children,
}: {
  scene: EditorScene;
  frame: SceneFrame;
  /** The name of the editor for a reader who cannot see it. */
  label: string;
  /**
   * What the editor sets, as the value of a slider: the layer is one, which also makes the viewer leave the arrow keys to
   * it, as it does for the other sliders.
   */
  value: { min: number; max: number; now: number; text: string };
  /** Attributes that say where the shapes stand, which the end-to-end scenarios read. */
  data?: Readonly<Record<string, string>>;
  onKeyDown?: (event: React.KeyboardEvent<HTMLDivElement>) => void;
  /** Called with the `deltaY` of a wheel turned with `Alt` held. */
  onAltWheel?: (deltaY: number) => void;
  /** What stands over the stage and takes no pointer, such as the labels of the halves. */
  overlay?: ReactNode;
  /** The shapes. */
  children: ReactNode;
}): React.JSX.Element {
  const element = useRef<HTMLDivElement>(null);
  const stage = useRef<Konva.Stage>(null);
  const panFrom = useRef<{ x: number; y: number } | null>(null);
  // The wheel handler is added once and reads the latest callback through a ref
  const altWheel = useRef(onAltWheel);
  useEffect(() => {
    altWheel.current = onAltWheel;
  });
  const { viewport } = scene.viewer;

  useEffect(() => {
    const root = element.current;
    if (root === null) {
      return;
    }
    // A listener added by the browser as passive could not stop the page from scrolling, so it is added by hand
    const onWheel = (event: WheelEvent): void => {
      event.preventDefault();
      const delta = event.deltaY === 0 ? event.deltaX : event.deltaY;
      if (event.altKey) {
        altWheel.current?.(delta);
        return;
      }
      const box = root.getBoundingClientRect();
      const at = new OpenSeadragon.Point(event.clientX - box.left, event.clientY - box.top);
      viewport.zoomBy(delta < 0 ? WHEEL_ZOOM : 1 / WHEEL_ZOOM, viewport.pointFromPixel(at, true));
      viewport.applyConstraints();
    };
    root.addEventListener('wheel', onWheel, { passive: false });
    return () => root.removeEventListener('wheel', onWheel);
  }, [viewport]);

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>): void => {
    // The canvas lets go of the focus on a click so that Space is not taken by a button, and this takes it back
    event.stopPropagation();
    event.currentTarget.focus();
    const box = event.currentTarget.getBoundingClientRect();
    const onShape = stage.current?.getIntersection({
      x: event.clientX - box.left,
      y: event.clientY - box.top,
    });
    if (onShape === null || onShape === undefined) {
      event.currentTarget.setPointerCapture(event.pointerId);
      panFrom.current = { x: event.clientX, y: event.clientY };
    }
  };

  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>): void => {
    const from = panFrom.current;
    if (from === null) {
      return;
    }
    const moved = new OpenSeadragon.Point(from.x - event.clientX, from.y - event.clientY);
    viewport.panBy(viewport.deltaPointsFromPixels(moved, true), true);
    viewport.applyConstraints(true);
    panFrom.current = { x: event.clientX, y: event.clientY };
  };

  return (
    <div
      ref={element}
      role="slider"
      aria-label={label}
      aria-valuemin={value.min}
      aria-valuemax={value.max}
      aria-valuenow={value.now}
      aria-valuetext={value.text}
      tabIndex={0}
      data-testid="editor-layer"
      {...Object.fromEntries(
        Object.entries(data ?? {}).map(([key, value]) => [`data-${key}`, value]),
      )}
      className="absolute inset-0 touch-none outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:ring-inset"
      onKeyDown={onKeyDown}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={() => {
        panFrom.current = null;
      }}
      onPointerCancel={() => {
        panFrom.current = null;
      }}
    >
      <Stage ref={stage} width={frame.stage.width} height={frame.stage.height}>
        <Layer>{children}</Layer>
      </Stage>
      {overlay === undefined ? null : (
        <div className="pointer-events-none absolute inset-0 overflow-hidden">{overlay}</div>
      )}
    </div>
  );
}
