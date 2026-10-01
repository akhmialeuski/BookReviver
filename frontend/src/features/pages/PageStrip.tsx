import { Link } from '@tanstack/react-router';
import { BookOpenIcon, FilePlusIcon, ListOrderedIcon, MoveIcon, PencilIcon } from 'lucide-react';
import { useState } from 'react';
import { AddPageDialog } from '@/features/pages/AddPageDialog';
import { MovePagesDialog, type MoveTarget } from '@/features/pages/MovePagesDialog';
import { useManifest } from '@/features/pages/manifest';
import { NumberPagesDialog } from '@/features/pages/NumberPagesDialog';
import { PageEditDialog } from '@/features/pages/PageEditDialog';
import { PageThumbnail } from '@/features/pages/PageThumbnail';
import { pruneSelection, selectRange } from '@/features/pages/selection';
import { describeError } from '@/shared/http/problem';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';
import { Badge } from '@/shared/ui/badge';
import { Button } from '@/shared/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/ui/card';
import { ErrorAlert } from '@/shared/ui/error-alert';
import { Pager } from '@/shared/ui/pager';

/**
 * The pages of the book as a grid of thumbnails, with the actions that arrange them.
 *
 * Every card shows the picture, the printed number or the position, the kind and whether the page is left out of
 * the book, and a click on the picture opens the page in the viewer. A page is chosen with its check mark, and a
 * shift-click chooses everything from the last chosen page, so a group is moved, numbered from, or added next to
 * with the buttons above the grid. The grid is read from the manifest query, so the events of the book and the
 * reader's own changes redraw it without a reload.
 */

const PAGE_SIZE = 48;

export function PageStrip({ projectId }: { projectId: string }): React.JSX.Element {
  const manifest = useManifest(projectId);
  const [gridPage, setGridPage] = useState(1);
  const [selectedRaw, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [lastClickedId, setLastClickedId] = useState<string | null>(null);
  const [editId, setEditId] = useState<string | null>(null);
  const [moveTarget, setMoveTarget] = useState<MoveTarget | null>(null);
  const [numbering, setNumbering] = useState(false);
  const [adding, setAdding] = useState(false);

  const pages = manifest.data ?? [];
  // A page that was deleted, here or by an event, leaves the selection with it
  const selected = pruneSelection(selectedRaw, pages);
  const selectedIds = pages.filter((page) => selected.has(page.id)).map((page) => page.id);
  const pageCount = Math.max(Math.ceil(pages.length / PAGE_SIZE), 1);
  const current = Math.min(gridPage, pageCount);
  const visible = pages.slice((current - 1) * PAGE_SIZE, current * PAGE_SIZE);

  const choose = (pageId: string, range: boolean): void => {
    const next = new Set(selected);
    if (range) {
      for (const id of selectRange(pages, lastClickedId, pageId)) {
        next.add(id);
      }
    } else if (next.has(pageId)) {
      next.delete(pageId);
    } else {
      next.add(pageId);
    }
    setSelected(next);
    setLastClickedId(pageId);
  };

  let body: React.JSX.Element;
  if (manifest.isError) {
    body = <ErrorAlert message={describeError(manifest.error)} />;
  } else if (manifest.data === undefined) {
    body = <p className="text-sm text-muted-foreground">{MESSAGES.common.loading}</p>;
  } else if (pages.length === 0) {
    body = <p className="text-sm text-muted-foreground">{MESSAGES.pages.strip.empty}</p>;
  } else {
    body = (
      <>
        <ul className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
          {visible.map((page) => {
            const name = MESSAGES.pages.name(page.position + 1, page.label);
            const checked = selected.has(page.id);
            return (
              <li
                key={page.id}
                className={cn(
                  'grid gap-1 rounded-md p-1 text-xs',
                  checked ? 'ring-2 ring-primary' : '',
                )}
                data-testid="page-card"
                data-page-id={page.id}
              >
                <div className="relative">
                  <Link
                    to="/projects/$projectId/viewer"
                    params={{ projectId }}
                    search={{ page: page.id }}
                    aria-label={MESSAGES.pages.strip.openPage(name)}
                    className="block rounded-md outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
                  >
                    <PageThumbnail page={page} alt={name} />
                  </Link>
                  {/* The click handler, not a change handler, because only a click knows about the shift key */}
                  <input
                    type="checkbox"
                    readOnly
                    checked={checked}
                    aria-label={MESSAGES.pages.strip.selectPage(name)}
                    onClick={(event) => choose(page.id, event.shiftKey)}
                    className="absolute top-1 left-1 size-5 cursor-pointer accent-primary"
                  />
                </div>
                <div className="flex items-center justify-between gap-1">
                  <span className="truncate font-medium" data-testid="page-name">
                    {page.label === '' ? MESSAGES.pages.position(page.position + 1) : page.label}
                  </span>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={MESSAGES.pages.strip.editPage(name)}
                    onClick={() => setEditId(page.id)}
                  >
                    <PencilIcon />
                  </Button>
                </div>
                <div className="flex flex-wrap gap-1">
                  <Badge variant="outline">{MESSAGES.pages.kinds[page.kind]}</Badge>
                  {page.included ? null : (
                    <Badge variant="secondary">{MESSAGES.pages.excluded}</Badge>
                  )}
                  {page.origin === 'scan' ? null : (
                    <Badge variant="secondary">{MESSAGES.pages.origins[page.origin]}</Badge>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
        <Pager page={current} pages={pageCount} onPageChange={setGridPage} />
      </>
    );
  }

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle>{MESSAGES.pages.strip.title}</CardTitle>
        <div className="flex flex-wrap items-center gap-2">
          {selectedIds.length > 0 ? (
            <>
              <span className="text-sm text-muted-foreground" data-testid="selected-count">
                {MESSAGES.pages.strip.selected(selectedIds.length)}
              </span>
              <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}>
                {MESSAGES.pages.strip.clearSelection}
              </Button>
            </>
          ) : (
            <Button
              variant="ghost"
              size="sm"
              disabled={pages.length === 0}
              onClick={() => setSelected(new Set(pages.map((page) => page.id)))}
            >
              {MESSAGES.pages.strip.selectAll}
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            disabled={selectedIds.length === 0}
            onClick={() => setMoveTarget({ pageIds: selectedIds })}
          >
            <MoveIcon />
            {MESSAGES.pages.strip.moveSelected}
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={pages.length === 0}
            onClick={() => setNumbering(true)}
          >
            <ListOrderedIcon />
            {MESSAGES.pages.strip.number}
          </Button>
          <Button variant="outline" size="sm" onClick={() => setAdding(true)}>
            <FilePlusIcon />
            {MESSAGES.pages.strip.add}
          </Button>
          <Button asChild variant="outline" size="sm">
            <Link to="/projects/$projectId/viewer" params={{ projectId }}>
              <BookOpenIcon />
              {MESSAGES.pages.strip.view}
            </Link>
          </Button>
        </div>
      </CardHeader>
      <CardContent className="grid gap-4">{body}</CardContent>

      <PageEditDialog
        projectId={projectId}
        page={pages.find((page) => page.id === editId) ?? null}
        onClose={() => setEditId(null)}
      />
      <MovePagesDialog
        projectId={projectId}
        pages={pages}
        target={moveTarget}
        onClose={() => setMoveTarget(null)}
      />
      <NumberPagesDialog
        projectId={projectId}
        pages={pages}
        open={numbering}
        firstId={selectedIds[0]}
        onClose={() => setNumbering(false)}
      />
      <AddPageDialog
        projectId={projectId}
        pages={pages}
        open={adding}
        anchorId={selectedIds.at(-1)}
        onClose={() => setAdding(false)}
      />
    </Card>
  );
}
