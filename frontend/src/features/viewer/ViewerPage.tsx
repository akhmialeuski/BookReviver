import { Link } from '@tanstack/react-router';
import { ArrowLeftIcon } from 'lucide-react';
import { useState } from 'react';
import type { PageSchema } from '@/api';
import { useManifest } from '@/features/pages/manifest';
import type { ViewerSearch } from '@/features/viewer/params';
import {
  lastViewStart,
  nextViewStart,
  previousViewStart,
  viewIndexes,
  viewStart,
} from '@/features/viewer/spread';
import { FitMode, type StagePage } from '@/features/viewer/stage';
import { ThumbnailPanel } from '@/features/viewer/ThumbnailPanel';
import { useViewerKeys } from '@/features/viewer/useViewerKeys';
import { StageState, useViewerStage } from '@/features/viewer/useViewerStage';
import { ViewerToolbar } from '@/features/viewer/ViewerToolbar';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The viewer screen: one page or a two-page spread of the book on an OpenSeadragon canvas, with the controls above
 * it and the page panel beside it.
 *
 * Which page is open and whether the spread is on are the search params of the route, so every state is a link and
 * survives a reload. The pages come from the manifest query, which the event stream keeps current, so a page that
 * is moved, relabelled or finished by a job changes here without a reload.
 */

const NO_PAGES: readonly PageSchema[] = [];

function stagePage(page: PageSchema | undefined): StagePage[] {
  return page === undefined ? [] : [{ id: page.id, infoUrl: page.images?.iiif_info ?? null }];
}

export function ViewerPage({
  projectId,
  search,
  onSearchChange,
}: {
  projectId: string;
  search: ViewerSearch;
  onSearchChange: (search: ViewerSearch) => void;
}): React.JSX.Element {
  const manifest = useManifest(projectId);
  const [fitMode, setFitMode] = useState<FitMode>(FitMode.Page);
  const [panelOpen, setPanelOpen] = useState(true);

  const pages = manifest.data ?? NO_PAGES;
  const count = pages.length;
  const spread = search.spread === true;
  const foundIndex = search.page === undefined ? 0 : pages.findIndex((p) => p.id === search.page);
  const currentIndex = Math.max(foundIndex, 0);
  const unknownPage = manifest.data !== undefined && search.page !== undefined && foundIndex < 0;

  const indexes = viewIndexes(currentIndex, count, spread);
  const first = indexes[0] ?? 0;
  const previous = previousViewStart(first, count, spread);
  const next = nextViewStart(first, count, spread);
  const shown = indexes.flatMap((index) => pages[index] ?? []);

  const view = shown.flatMap((page) => stagePage(page));
  const around = [next, previous].flatMap((start) =>
    start === null ? [] : [viewIndexes(start, count, spread).flatMap((i) => stagePage(pages[i]))],
  );
  const stage = useViewerStage(view, around, fitMode);

  const openIndex = (index: number): void => {
    const clamped = Math.min(Math.max(index, 0), Math.max(count - 1, 0));
    const target = pages[viewStart(clamped, spread)];
    if (target !== undefined) {
      onSearchChange({ page: target.id, ...(spread ? { spread: true } : {}) });
    }
  };
  const openStart = (start: number | null): void => {
    if (start !== null) {
      openIndex(start);
    }
  };

  useViewerKeys({
    previous: () => openStart(previous),
    next: () => openStart(next),
    first: () => openIndex(0),
    last: () => openStart(lastViewStart(count, spread)),
  });

  if (manifest.isError) {
    return <ErrorAlert message={describeError(manifest.error)} />;
  }
  if (manifest.data === undefined) {
    return <p className="p-4 text-sm text-muted-foreground">{MESSAGES.viewer.loading}</p>;
  }

  const shownIds = new Set(shown.map((page) => page.id));
  const missingImage = shown.some((page) => page.images === null);

  return (
    <div className="flex h-[calc(100dvh-3.5rem)] min-h-96 flex-col">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 pt-2">
        <Link
          to="/projects/$projectId"
          params={{ projectId }}
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeftIcon className="size-4" />
          {MESSAGES.viewer.back}
        </Link>
        <p className="text-sm font-medium" data-testid="viewer-caption" aria-live="polite">
          {shown
            .map((page) => MESSAGES.viewer.caption(page.label, page.position + 1, count))
            .join('   |   ')}
        </p>
      </div>

      {count === 0 ? (
        <p className="p-4 text-sm text-muted-foreground">{MESSAGES.viewer.empty}</p>
      ) : (
        <>
          <ViewerToolbar
            count={count}
            index={first}
            spread={spread}
            fitMode={fitMode}
            panelOpen={panelOpen}
            hasPrevious={previous !== null}
            hasNext={next !== null}
            onOpenIndex={openIndex}
            onPrevious={() => openStart(previous)}
            onNext={() => openStart(next)}
            onFitMode={(mode) => {
              setFitMode(mode);
              stage.fit(mode);
            }}
            onZoomIn={stage.zoomIn}
            onZoomOut={stage.zoomOut}
            onToggleSpread={() => {
              const page = pages[currentIndex];
              if (page !== undefined) {
                onSearchChange({ page: page.id, ...(spread ? {} : { spread: true }) });
              }
            }}
            onTogglePanel={() => setPanelOpen((open) => !open)}
          />
          {unknownPage ? (
            <p className="px-3 pt-2 text-sm text-muted-foreground">{MESSAGES.viewer.unknownPage}</p>
          ) : null}
          <div className="flex min-h-0 flex-1">
            <div className="relative min-w-0 flex-1">
              <div
                ref={stage.containerRef}
                className="absolute inset-0 bg-muted"
                data-testid="viewer-canvas"
                data-state={stage.state}
                data-page-ids={shown.map((page) => page.id).join(',')}
              />
              {missingImage || stage.state === StageState.Failed ? (
                <p className="absolute inset-x-0 top-3 mx-auto w-fit rounded-md bg-background/90 px-3 py-1 text-sm shadow">
                  {stage.state === StageState.Failed
                    ? MESSAGES.viewer.loadFailed
                    : MESSAGES.viewer.noImage}
                </p>
              ) : null}
            </div>
            {panelOpen ? (
              <aside className="flex w-36 shrink-0 flex-col border-l sm:w-44">
                <ThumbnailPanel
                  pages={pages}
                  shownIds={shownIds}
                  onOpen={(index) => openIndex(index)}
                />
              </aside>
            ) : null}
          </div>
        </>
      )}
    </div>
  );
}
