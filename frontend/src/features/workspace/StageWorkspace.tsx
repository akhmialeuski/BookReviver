import { PanelLeftIcon, PanelRightIcon } from 'lucide-react';
import { useDefaultLayout, usePanelRef } from 'react-resizable-panels';
import { browserStorage } from '@/features/workspace/storage';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@/shared/ui/resizable';

/**
 * The three parts of a stage screen: the strip of pages on the left, the canvas in the middle and the panel of the
 * stage on the right.
 *
 * A stage hands over the content of each part, and a stage that lays out its pages differently, such as the grid of
 * pages, leaves the strip out and gets a layout of two parts. The width of the parts is dragged on the handles,
 * collapses when a side is dragged shut or its button is pressed, and is remembered in the browser, one record for the
 * layout with the strip and one for the layout without it. The row above the canvas holds the two buttons and, between
 * them, whatever the stage says about the open page.
 */

const PANEL_ID = { strip: 'strip', canvas: 'canvas', panel: 'panel' } as const;
const LAYOUT_ID = 'bookreviver.workspace';

const STRIP_SIZE = { default: '18%', min: '12%', max: '32%' } as const;
const PANEL_SIZE = { default: '26%', min: '18%', max: '42%' } as const;
const CANVAS_MIN = '30%';

export function StageWorkspace({
  strip,
  canvasHeader,
  canvas,
  panel,
}: {
  /** The strip of pages, or null for a stage that has none. */
  strip: React.ReactNode | null;
  /** What stands in the row above the canvas, between the two buttons. */
  canvasHeader?: React.ReactNode;
  canvas: React.ReactNode;
  panel: React.ReactNode;
}): React.JSX.Element {
  const hasStrip = strip !== null;
  const stripRef = usePanelRef();
  const panelRef = usePanelRef();
  const layout = useDefaultLayout({
    id: hasStrip ? LAYOUT_ID : `${LAYOUT_ID}.without-strip`,
    storage: browserStorage,
    panelIds: hasStrip
      ? [PANEL_ID.strip, PANEL_ID.canvas, PANEL_ID.panel]
      : [PANEL_ID.canvas, PANEL_ID.panel],
  });

  const toggle = (ref: typeof stripRef): void => {
    if (ref.current?.isCollapsed()) {
      ref.current.expand();
    } else {
      ref.current?.collapse();
    }
  };

  return (
    <ResizablePanelGroup
      orientation="horizontal"
      defaultLayout={layout.defaultLayout}
      onLayoutChanged={layout.onLayoutChanged}
      id="workspace"
    >
      {hasStrip ? (
        <>
          <ResizablePanel
            id={PANEL_ID.strip}
            panelRef={stripRef}
            defaultSize={STRIP_SIZE.default}
            minSize={STRIP_SIZE.min}
            maxSize={STRIP_SIZE.max}
            collapsible
            collapsedSize={0}
          >
            {strip}
          </ResizablePanel>
          <ResizableHandle withHandle />
        </>
      ) : null}
      <ResizablePanel id={PANEL_ID.canvas} minSize={CANVAS_MIN}>
        <div className="flex size-full flex-col">
          <div className="flex h-11 shrink-0 items-center gap-2 border-b px-2">
            {hasStrip ? (
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={MESSAGES.workspace.layout.toggleStrip}
                title={MESSAGES.workspace.layout.toggleStrip}
                data-testid="toggle-strip"
                onClick={() => toggle(stripRef)}
              >
                <PanelLeftIcon />
              </Button>
            ) : null}
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">{canvasHeader}</div>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={MESSAGES.workspace.layout.togglePanel}
              title={MESSAGES.workspace.layout.togglePanel}
              data-testid="toggle-panel"
              onClick={() => toggle(panelRef)}
            >
              <PanelRightIcon />
            </Button>
          </div>
          <div className="min-h-0 flex-1">{canvas}</div>
        </div>
      </ResizablePanel>
      <ResizableHandle withHandle />
      <ResizablePanel
        id={PANEL_ID.panel}
        panelRef={panelRef}
        defaultSize={PANEL_SIZE.default}
        minSize={PANEL_SIZE.min}
        maxSize={PANEL_SIZE.max}
        collapsible
        collapsedSize={0}
      >
        {panel}
      </ResizablePanel>
    </ResizablePanelGroup>
  );
}
