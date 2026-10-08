import { Link } from '@tanstack/react-router';
import { ArrowLeftIcon, MoveIcon, PencilIcon } from 'lucide-react';
import { useRef, useState } from 'react';
import type { PageSchema, Stage } from '@/api';
import { MovePagesDialog } from '@/features/pages/MovePagesDialog';
import { useManifest } from '@/features/pages/manifest';
import { PageEditDialog } from '@/features/pages/PageEditDialog';
import { PageCanvas, type PageCanvasHandle } from '@/features/viewer/PageCanvas';
import type { ViewerSearch } from '@/features/viewer/params';
import { FitMode, type StagePage } from '@/features/viewer/stage';
import { ThumbnailPanel } from '@/features/viewer/ThumbnailPanel';
import { usePageNavigation } from '@/features/viewer/usePageNavigation';
import { ViewerToolbar } from '@/features/viewer/ViewerToolbar';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Badge } from '@/shared/ui/badge';
import { Button } from '@/shared/ui/button';
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

function stagePage(page: PageSchema): StagePage {
  return { id: page.id, infoUrl: page.images?.iiif_info ?? null };
}

export function ViewerPage({
  projectId,
  stage,
  search,
  onSearchChange,
}: {
  projectId: string;
  /** The stage the reader worked on before reading, which the way back leads to. */
  stage: Stage;
  search: ViewerSearch;
  onSearchChange: (search: ViewerSearch) => void;
}): React.JSX.Element {
  const manifest = useManifest(projectId);
  const [fitMode, setFitMode] = useState<FitMode>(FitMode.Page);
  const [panelOpen, setPanelOpen] = useState(true);
  const [editId, setEditId] = useState<string | null>(null);
  const [moveIds, setMoveIds] = useState<readonly string[] | null>(null);

  const pages = manifest.data ?? NO_PAGES;
  const count = pages.length;
  const spread = search.spread === true;
  const navigation = usePageNavigation(
    pages,
    (page) => page.id,
    search.page,
    spread,
    (id) => onSearchChange({ page: id, ...(spread ? { spread: true } : {}) }),
  );
  const { currentIndex, first, shown, openIndex } = navigation;

  const view = shown.map(stagePage);
  const around = navigation.around.map((group) => group.map(stagePage));
  const canvas = useRef<PageCanvasHandle>(null);

  if (manifest.isError) {
    return <ErrorAlert message={describeError(manifest.error)} />;
  }
  if (manifest.data === undefined) {
    return <p className="p-4 text-sm text-muted-foreground">{MESSAGES.viewer.loading}</p>;
  }

  const shownIds = new Set(shown.map((page) => page.id));

  return (
    <div className="flex h-full min-h-96 flex-col">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 pt-2">
        <Link
          to="/projects/$projectId/stages/$stage"
          params={{ projectId, stage }}
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
        {shown.map((page) => {
          const name = MESSAGES.pages.name(page.position + 1, page.label);
          return (
            <div key={page.id} className="flex items-center gap-1">
              <Button
                variant="ghost"
                size="sm"
                aria-label={MESSAGES.viewer.actions.edit(name)}
                onClick={() => setEditId(page.id)}
              >
                <PencilIcon />
                {MESSAGES.viewer.actions.editShort}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                aria-label={MESSAGES.viewer.actions.move(name)}
                onClick={() => setMoveIds([page.id])}
              >
                <MoveIcon />
                {MESSAGES.viewer.actions.moveShort}
              </Button>
            </div>
          );
        })}
        {shown.some((page) => !page.included) ? (
          <Badge variant="secondary">{MESSAGES.pages.excluded}</Badge>
        ) : null}
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
            hasPrevious={navigation.hasPrevious}
            hasNext={navigation.hasNext}
            onOpenIndex={openIndex}
            onPrevious={navigation.openPrevious}
            onNext={navigation.openNext}
            onFitMode={(mode) => {
              setFitMode(mode);
              canvas.current?.fit(mode);
            }}
            onZoomIn={() => canvas.current?.zoomIn()}
            onZoomOut={() => canvas.current?.zoomOut()}
            onToggleSpread={() => {
              const page = pages[currentIndex];
              if (page !== undefined) {
                onSearchChange({ page: page.id, ...(spread ? {} : { spread: true }) });
              }
            }}
            onTogglePanel={() => setPanelOpen((open) => !open)}
          />
          {navigation.unknownPage ? (
            <p className="px-3 pt-2 text-sm text-muted-foreground">{MESSAGES.viewer.unknownPage}</p>
          ) : null}
          <div className="flex min-h-0 flex-1">
            <div className="min-w-0 flex-1">
              <PageCanvas view={view} around={around} fitMode={fitMode} handle={canvas} />
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
      <PageEditDialog
        projectId={projectId}
        page={pages.find((page) => page.id === editId) ?? null}
        onClose={() => setEditId(null)}
      />
      <MovePagesDialog
        projectId={projectId}
        pages={pages}
        target={moveIds === null ? null : { pageIds: moveIds }}
        onClose={() => setMoveIds(null)}
      />
    </div>
  );
}
