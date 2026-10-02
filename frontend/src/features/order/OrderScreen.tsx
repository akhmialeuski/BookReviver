import { useNavigate } from '@tanstack/react-router';
import { useEffect, useMemo, useState } from 'react';
import type { PageSchema } from '@/api';
import { AttachDialog } from '@/features/order/AttachDialog';
import { DeletePagesDialog } from '@/features/order/DeletePagesDialog';
import { insertBody, missingPageBodies } from '@/features/order/insert';
import { gapKey } from '@/features/order/layout';
import { NumberingPanel } from '@/features/order/NumberingPanel';
import { type NumberingDraft, newDraft } from '@/features/order/numbering';
import { type FocusRequest, OrderGrid } from '@/features/order/OrderGrid';
import { OrderToolbar, TILE_SIZE } from '@/features/order/OrderToolbar';
import { placesToCheck } from '@/features/order/places';
import { SelectionPanel } from '@/features/order/SelectionPanel';
import { usePreviewLabels } from '@/features/order/usePreviewLabels';
import { useCreatePages, useMovePages } from '@/features/pages/actions';
import { describePageError } from '@/features/pages/errors';
import { findGaps, type LabelGap } from '@/features/pages/gaps';
import { MovePagesDialog, type MoveTarget } from '@/features/pages/MovePagesDialog';
import { useManifest } from '@/features/pages/manifest';
import { anchorBody, pageIdsOfSource } from '@/features/pages/order';
import { pruneSelection } from '@/features/pages/selection';
import { type StageSearch, ViewMode } from '@/features/workspace/params';
import { StageWorkspace } from '@/features/workspace/StageWorkspace';
import {
  type ClickModifiers,
  type SelectionState,
  selectionAfterClick,
} from '@/features/workspace/selection';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The Order stage of a book: the grid of its pages with the toolbar above it, and on the right the panel of the
 * selected pages or the numbering.
 *
 * The screen owns what the grid and the panel share: the selected pages, the numbering being drafted, the dialogs and
 * the actions that add and move pages. The pages are read from the manifest the viewer shares, so the events of the
 * book and the reader's own changes redraw the grid without a reload. `?source=<id>` opens the stage with the pages of
 * that file selected, which is how the Import stage points at the pages it made. `?view=spread` shows spreads.
 */

const NO_PAGES: readonly PageSchema[] = [];
const NOTHING_SELECTED: SelectionState = { selected: new Set(), anchorId: null };

export function OrderScreen({
  projectId,
  search,
  onSearchChange,
}: {
  projectId: string;
  search: StageSearch;
  /** Merge changes into the search params of the route; an undefined value takes the param out. */
  onSearchChange: (changes: Partial<StageSearch>) => void;
}): React.JSX.Element {
  const navigate = useNavigate();
  const manifest = useManifest(projectId);
  const movePages = useMovePages(projectId);
  const createPages = useCreatePages(projectId);
  const pages = manifest.data ?? NO_PAGES;

  // What the reader picked, kept with the file the address named, so a new address starts from its own pages
  const [picked, setPicked] = useState<{ source?: string; state: SelectionState } | null>(null);
  const [size, setSize] = useState<number>(TILE_SIZE.initial);
  const [draft, setDraft] = useState<NumberingDraft | null>(null);
  const [moveTarget, setMoveTarget] = useState<MoveTarget | null>(null);
  const [attachPage, setAttachPage] = useState<PageSchema | null>(null);
  const [deleteIds, setDeleteIds] = useState<readonly string[] | null>(null);
  const [adding, setAdding] = useState<string | null>(null);
  const [focus, setFocus] = useState<FocusRequest | null>(null);

  const spread = search.view === ViewMode.Spread;
  const sourceIds = useMemo(
    () => (search.source === undefined ? [] : pageIdsOfSource(pages, search.source)),
    [pages, search.source],
  );
  const firstOfSource = sourceIds[0];
  const selection = useMemo(() => {
    const state =
      picked !== null && picked.source === search.source
        ? picked.state
        : { selected: new Set(sourceIds), anchorId: firstOfSource ?? null };
    return { ...state, selected: pruneSelection(state.selected, pages) };
  }, [picked, search.source, sourceIds, firstOfSource, pages]);
  const selectedPages = useMemo(
    () => pages.filter((page) => selection.selected.has(page.id)),
    [pages, selection.selected],
  );
  const selectedIds = selectedPages.map((page) => page.id);

  // The pages of the file the address names come into view once, when they are first known
  useEffect(() => {
    if (search.source !== undefined && firstOfSource !== undefined) {
      setFocus({ cellId: firstOfSource, nonce: 0 });
    }
  }, [search.source, firstOfSource]);

  const gaps = useMemo(() => findGaps(pages), [pages]);
  const missing = useMemo(
    () => pages.filter((page) => page.included && page.origin === 'placeholder'),
    [pages],
  );
  const places = useMemo(() => placesToCheck(pages, gaps), [pages, gaps]);
  const preview = usePreviewLabels(projectId, pages, draft, manifest.dataUpdatedAt);

  // The last change that the server refused: a move, or the adding of pages
  const failed = movePages.isError || createPages.isError;
  const failure: unknown = movePages.isError ? movePages.error : createPages.error;

  const select = (pageId: string, click: ClickModifiers): void =>
    setPicked({
      source: search.source,
      state: selectionAfterClick(pages, selection, pageId, click),
    });

  const addMissing = (toFill: readonly LabelGap[], key: string): void => {
    setAdding(key);
    createPages.mutate(toFill.flatMap(missingPageBodies), { onSettled: () => setAdding(null) });
  };

  const numberFrom = (firstId: string | undefined): void => setDraft(newDraft(pages, firstId));

  const showPlace = (index: number): void => {
    const place = places[index];
    if (place !== undefined) {
      setFocus({ cellId: place.cellId, nonce: Date.now() });
    }
  };

  if (manifest.isError) {
    return <ErrorAlert message={describeError(manifest.error)} />;
  }
  if (manifest.data === undefined) {
    return <p className="p-4 text-sm text-muted-foreground">{MESSAGES.common.loading}</p>;
  }

  const canvas = (
    <div className="flex size-full flex-col" data-testid="order-screen">
      <OrderToolbar
        spread={spread}
        numbering={draft !== null}
        hasSelection={selectedIds.length > 0}
        size={size}
        onSpread={(value) => onSearchChange({ view: value ? ViewMode.Spread : undefined })}
        onNumber={() => (draft === null ? numberFrom(selectedIds[0]) : setDraft(null))}
        onInsert={(spec) => createPages.mutate([insertBody(spec, selectedIds)])}
        onSize={setSize}
      />
      {draft === null ? null : (
        <p
          className="mx-4 mt-3 rounded-md border border-blue-300 bg-blue-50 p-2 text-sm text-blue-900"
          data-testid="numbering-preview-note"
        >
          {MESSAGES.order.numbering.preview}
        </p>
      )}
      <OrderGrid
        pages={pages}
        gaps={gaps}
        selected={selection.selected}
        spread={spread}
        size={size}
        previewLabels={draft === null ? undefined : preview.data}
        addingGapKey={adding}
        moveError={failed ? describePageError(failure) : null}
        focus={focus}
        onDismissError={() => {
          movePages.reset();
          createPages.reset();
        }}
        onSelect={select}
        onOpen={(pageId) =>
          void navigate({
            to: '/projects/$projectId/viewer',
            params: { projectId },
            search: { page: pageId },
          })
        }
        onMove={(ids, anchor) =>
          movePages.mutate({
            path: { project_id: projectId },
            body: { page_ids: [...ids], ...anchorBody(anchor) },
          })
        }
        onAddMissing={(gap) => addMissing([gap], gapKey(gap))}
      />
    </div>
  );

  const panel =
    draft === null ? (
      <SelectionPanel
        projectId={projectId}
        selected={selectedPages}
        gaps={gaps}
        missing={missing}
        places={places}
        onClear={() => setPicked({ source: search.source, state: NOTHING_SELECTED })}
        onMove={() => setMoveTarget({ pageIds: selectedIds })}
        onNumber={() => numberFrom(selectedIds[0])}
        onInsert={(spec) => createPages.mutate([insertBody(spec, selectedIds)])}
        onAttach={setAttachPage}
        onDelete={() => setDeleteIds(selectedIds)}
        onShowPlace={showPlace}
      />
    ) : (
      <NumberingPanel
        projectId={projectId}
        pages={pages}
        draft={draft}
        preview={preview}
        gaps={gaps}
        adding={adding !== null}
        onDraft={setDraft}
        onClose={() => setDraft(null)}
        onAddMissing={(toFill) => addMissing(toFill, 'numbering')}
      />
    );

  return (
    <>
      <StageWorkspace strip={null} canvas={canvas} panel={panel} />
      <MovePagesDialog
        projectId={projectId}
        pages={pages}
        target={moveTarget}
        onClose={() => setMoveTarget(null)}
      />
      <AttachDialog projectId={projectId} page={attachPage} onClose={() => setAttachPage(null)} />
      <DeletePagesDialog
        projectId={projectId}
        pageIds={deleteIds}
        onClose={() => setDeleteIds(null)}
      />
    </>
  );
}
