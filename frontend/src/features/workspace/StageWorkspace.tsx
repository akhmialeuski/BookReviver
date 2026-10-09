import { PanelLeftIcon, PanelRightIcon } from 'lucide-react';
import { useState } from 'react';
import { useDefaultLayout, usePanelRef } from 'react-resizable-panels';
import { browserStorage } from '@/features/workspace/storage';
import { StripSheetProvider } from '@/features/workspace/stripSheet';
import { useIsNarrow } from '@/shared/hooks/useMediaQuery';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@/shared/ui/resizable';
import { Sheet, SheetContent, SheetTitle } from '@/shared/ui/sheet';

/**
 * The three parts of a stage screen: the strip of pages on the left, the canvas in the middle and the panel of the
 * stage on the right.
 *
 * A stage hands over the content of each part, and a stage that lays out its pages differently, such as the grid of
 * pages, leaves the strip out and gets a layout of two parts. In a window as wide as the `lg` breakpoint of Tailwind or
 * wider, the width of the parts is dragged on the handles, collapses when a side is dragged shut or its button is
 * pressed, and is remembered in the browser, one record for the layout with the strip and one for the layout without
 * it. In a narrower window the canvas takes the whole width, and the same two buttons open the strip and the panel as
 * sheets over it. The wide layout is a component of its own, so it reads the remembered widths again whenever the window
 * grows back, and the narrow layout never writes them. The two buttons stand in the bar of the steps of the stage, the
 * one of the strip at its start and the one of the panel at its end; a stage without a bar of steps has a slim row above
 * its canvas, with the two buttons and, between them, whatever the stage says in `canvasHeader`.
 */

const PANEL_ID = { strip: 'strip', canvas: 'canvas', panel: 'panel' } as const;
const LAYOUT_ID = 'bookreviver.workspace';

const STRIP_SIZE = { default: '18%', min: '12%', max: '32%' } as const;
const PANEL_SIZE = { default: '26%', min: '18%', max: '42%' } as const;
const CANVAS_MIN = '30%';

/** The two buttons that show and hide the side panels, for the bar of the steps to place. */
export interface PanelToggles {
  /** The button of the strip, or null for a stage that has none. */
  strip: React.ReactNode;
  panel: React.ReactNode;
}

interface WorkspaceParts {
  /** The strip of pages, or null for a stage that has none. */
  strip: React.ReactNode | null;
  /** What stands between the two buttons in the slim row of a stage without a bar of steps. */
  canvasHeader?: React.ReactNode;
  /**
   * The row of the steps of the stage, given the two buttons to place at its start and its end, or null for a stage that
   * has none, which then gets a row of the two buttons alone.
   */
  stepBar?: ((toggles: PanelToggles) => React.ReactNode) | null;
  canvas: React.ReactNode;
  panel: React.ReactNode;
}

/** The canvas under the bar of its steps, which the wide and the narrow layout both draw. */
function CanvasColumn({
  hasStrip,
  canvasHeader,
  stepBar,
  canvas,
  onToggleStrip,
  onTogglePanel,
}: {
  hasStrip: boolean;
  canvasHeader: React.ReactNode;
  stepBar: WorkspaceParts['stepBar'];
  canvas: React.ReactNode;
  onToggleStrip: () => void;
  onTogglePanel: () => void;
}): React.JSX.Element {
  const toggles: PanelToggles = {
    strip: hasStrip ? (
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label={MESSAGES.workspace.layout.toggleStrip}
        title={MESSAGES.workspace.layout.toggleStrip}
        data-testid="toggle-strip"
        onClick={onToggleStrip}
      >
        <PanelLeftIcon />
      </Button>
    ) : null,
    panel: (
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label={MESSAGES.workspace.layout.togglePanel}
        title={MESSAGES.workspace.layout.togglePanel}
        data-testid="toggle-panel"
        onClick={onTogglePanel}
      >
        <PanelRightIcon />
      </Button>
    ),
  };
  return (
    <div className="flex size-full flex-col">
      {stepBar === null || stepBar === undefined ? (
        <div className="flex h-11 shrink-0 items-center gap-2 border-b px-2">
          {toggles.strip}
          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">{canvasHeader}</div>
          {toggles.panel}
        </div>
      ) : (
        stepBar(toggles)
      )}
      <div className="min-h-0 flex-1">{canvas}</div>
    </div>
  );
}

function WideWorkspace({
  strip,
  canvasHeader,
  stepBar,
  canvas,
  panel,
}: WorkspaceParts): React.JSX.Element {
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
        <CanvasColumn
          hasStrip={hasStrip}
          canvasHeader={canvasHeader}
          stepBar={stepBar}
          canvas={canvas}
          onToggleStrip={() => toggle(stripRef)}
          onTogglePanel={() => toggle(panelRef)}
        />
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

function NarrowWorkspace({
  strip,
  canvasHeader,
  stepBar,
  canvas,
  panel,
}: WorkspaceParts): React.JSX.Element {
  const [stripOpen, setStripOpen] = useState(false);
  const [panelOpen, setPanelOpen] = useState(false);

  return (
    <>
      <CanvasColumn
        hasStrip={strip !== null}
        canvasHeader={canvasHeader}
        stepBar={stepBar}
        canvas={canvas}
        onToggleStrip={() => setStripOpen((open) => !open)}
        onTogglePanel={() => setPanelOpen((open) => !open)}
      />
      {strip === null ? null : (
        <Sheet open={stripOpen} onOpenChange={setStripOpen}>
          <SheetContent
            side="left"
            className="pt-9"
            aria-describedby={undefined}
            data-testid="strip-sheet"
          >
            <SheetTitle className="sr-only">{MESSAGES.workspace.strip.title}</SheetTitle>
            <StripSheetProvider value={() => setStripOpen(false)}>{strip}</StripSheetProvider>
          </SheetContent>
        </Sheet>
      )}
      <Sheet open={panelOpen} onOpenChange={setPanelOpen}>
        <SheetContent side="right" aria-describedby={undefined} data-testid="panel-sheet">
          <SheetTitle className="sr-only">{MESSAGES.workspace.layout.panelTitle}</SheetTitle>
          {panel}
        </SheetContent>
      </Sheet>
    </>
  );
}

export function StageWorkspace(parts: WorkspaceParts): React.JSX.Element {
  return useIsNarrow() ? <NarrowWorkspace {...parts} /> : <WideWorkspace {...parts} />;
}
