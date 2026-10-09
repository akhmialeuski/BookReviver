import { useVirtualizer } from '@tanstack/react-virtual';
import { ArrowDownUpIcon, ChevronRightIcon, SearchIcon } from 'lucide-react';
import { Fragment, useEffect, useRef, useState } from 'react';
import type { PageSchema } from '@/api';
import { useMovePages, useMoveSourcePages } from '@/features/pages/actions';
import { describePageError } from '@/features/pages/errors';
import { shortName } from '@/features/pages/names';
import { AnchorSide, anchorBody, readingWindow } from '@/features/pages/order';
import { PageThumbnail } from '@/features/pages/PageThumbnail';
import { SegmentedRadio } from '@/features/pages/SegmentedRadio';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/ui/dialog';
import { ErrorAlert } from '@/shared/ui/error-alert';
import { Input } from '@/shared/ui/input';

/**
 * Asks where a page, a group of pages or all the pages of a source should go, and moves them.
 *
 * The place is a page of the book and a side of it, chosen on a strip of thumbnails or found by typing its printed
 * number or its place in the book. The pages that move are shown on the strip but cannot be chosen, since a place next
 * to oneself is not defined. Before anything is sent, the dialog writes how the book will read around the new place,
 * and the button names the move in full. A group keeps the order it has in the book, and a source is moved with one
 * request, which puts a missing part of the book, such as a cover file, in its place.
 */

const STRIP_TILE_PX = 72;
const STRIP_OVERSCAN = 8;

export interface MoveTarget {
  /** Ids of the pages to move, or of the pages of the source when `sourceId` is set. */
  pageIds: readonly string[];
  /** Set to move every page of a source with the one request made for it. */
  sourceId?: string;
  /** What the dialog calls what moves, such as the file name of a source. */
  sourceName?: string;
}

/** Find the page a search names: the page with that printed number, else the page at that place in the book. */
function findPage(pages: readonly PageSchema[], moving: ReadonlySet<string>, query: string) {
  const wanted = query
    .trim()
    .replace(/^(p\.|#)\s*/i, '')
    .toLowerCase();
  if (wanted === '') {
    return undefined;
  }
  const candidates = pages.filter((page) => !moving.has(page.id));
  return (
    candidates.find((page) => page.label.toLowerCase() === wanted) ??
    candidates.find((page) => String(page.position + 1) === wanted)
  );
}

function MoveForm({
  projectId,
  pages,
  thumbnails,
  target,
  onDone,
}: {
  projectId: string;
  pages: readonly PageSchema[];
  thumbnails: ReadonlyMap<string, string> | undefined;
  target: MoveTarget;
  onDone: () => void;
}): React.JSX.Element {
  const text = MESSAGES.order.move;
  const movePages = useMovePages(projectId);
  const moveSource = useMoveSourcePages(projectId);
  const moving = new Set(target.pageIds);
  const candidates = pages.filter((page) => !moving.has(page.id));
  const [anchorId, setAnchorId] = useState(candidates[0]?.id ?? '');
  const [side, setSide] = useState<AnchorSide>(AnchorSide.After);
  const [query, setQuery] = useState('');
  const mutation = target.sourceId === undefined ? movePages : moveSource;
  const anchor = pages.find((page) => page.id === anchorId);
  const anchorIndex = pages.findIndex((page) => page.id === anchorId);
  const reading =
    anchor === undefined ? null : readingWindow(pages, moving, { pageId: anchor.id, side });
  const noMatch = query.trim() !== '' && findPage(pages, moving, query) === undefined;

  const scroller = useRef<HTMLFieldSetElement>(null);
  const strip = useVirtualizer({
    count: pages.length,
    horizontal: true,
    getScrollElement: () => scroller.current,
    estimateSize: () => STRIP_TILE_PX,
    overscan: STRIP_OVERSCAN,
  });
  useEffect(() => {
    if (anchorIndex >= 0) {
      strip.scrollToIndex(anchorIndex, { align: 'center' });
    }
  }, [anchorIndex, strip]);

  const submit = (event: React.FormEvent): void => {
    event.preventDefault();
    const place = anchorBody({ pageId: anchorId, side });
    if (target.sourceId === undefined) {
      movePages.mutate(
        { path: { project_id: projectId }, body: { page_ids: [...target.pageIds], ...place } },
        { onSuccess: onDone },
      );
    } else {
      moveSource.mutate(
        { path: { project_id: projectId, source_id: target.sourceId }, body: place },
        { onSuccess: onDone },
      );
    }
  };

  if (candidates.length === 0 || anchor === undefined) {
    return (
      <div className="grid gap-4">
        <p className="text-sm text-muted-foreground">{text.noAnchor}</p>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onDone}>
            {MESSAGES.common.cancel}
          </Button>
        </DialogFooter>
      </div>
    );
  }

  return (
    <form className="grid gap-4" onSubmit={submit}>
      <div className="flex flex-wrap items-center gap-3">
        <SegmentedRadio
          legend={text.side}
          hideLegend
          value={side}
          options={[
            { value: AnchorSide.Before, label: text.before },
            { value: AnchorSide.After, label: text.after },
          ]}
          onChange={setSide}
        />
        <div className="relative min-w-48 flex-1">
          <SearchIcon
            className="pointer-events-none absolute top-2.5 left-3 size-4 text-muted-foreground"
            aria-hidden="true"
          />
          <Input
            value={query}
            aria-label={text.searchLabel}
            placeholder={text.search}
            className="pl-9"
            onChange={(event) => {
              setQuery(event.target.value);
              const found = findPage(pages, moving, event.target.value);
              if (found !== undefined) {
                setAnchorId(found.id);
              }
            }}
          />
        </div>
      </div>
      {noMatch ? <p className="-mt-2 text-xs text-muted-foreground">{text.noMatch}</p> : null}

      <fieldset
        ref={scroller}
        className="h-28 min-w-0 overflow-x-auto rounded-md border bg-muted/30"
        data-testid="move-strip"
      >
        <legend className="sr-only">{text.strip}</legend>
        <div className="relative h-full" style={{ width: strip.getTotalSize() }}>
          {strip.getVirtualItems().map((item) => {
            const page = pages[item.index];
            if (page === undefined) {
              return null;
            }
            const stays = moving.has(page.id);
            const chosen = page.id === anchorId;
            return (
              <button
                key={page.id}
                type="button"
                disabled={stays}
                aria-pressed={chosen}
                aria-label={
                  stays ? `${shortName(page)}. ${text.moving}` : text.stripPage(shortName(page))
                }
                onClick={() => setAnchorId(page.id)}
                data-testid="move-strip-page"
                data-page-id={page.id}
                className={cn(
                  'absolute top-0 grid h-full w-16 content-center gap-1 p-1 text-xs outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50',
                  stays ? 'opacity-40' : 'hover:bg-accent/60',
                )}
                style={{ transform: `translateX(${item.start + (STRIP_TILE_PX - 64) / 2}px)` }}
              >
                {chosen ? (
                  <span
                    aria-hidden="true"
                    data-testid="move-bar"
                    data-side={side}
                    className={cn(
                      'absolute inset-y-1 w-1 rounded-full bg-blue-500',
                      side === AnchorSide.Before ? '-left-0.5' : '-right-0.5',
                    )}
                  />
                ) : null}
                <PageThumbnail
                  page={page}
                  src={thumbnails === undefined ? undefined : (thumbnails.get(page.id) ?? null)}
                  alt=""
                  className={cn('w-full', chosen ? 'ring-2 ring-foreground' : '')}
                />
                <span className={cn('truncate text-center', chosen ? 'font-medium' : '')}>
                  {shortName(page)}
                </span>
              </button>
            );
          })}
        </div>
      </fieldset>

      <section className="grid gap-2" aria-label={text.reading}>
        <h3 className="text-sm font-medium">{text.reading}</h3>
        <ol className="flex flex-wrap items-center gap-1 text-sm" data-testid="move-reading">
          {(reading ?? []).map((entry, index) => (
            <Fragment key={entry.kind === 'page' ? entry.page.id : `more-${index}`}>
              {index === 0 ? null : (
                <ChevronRightIcon className="size-3.5 text-muted-foreground" aria-hidden="true" />
              )}
              <li
                className={cn(
                  'rounded-full px-2.5 py-0.5',
                  entry.kind === 'page' && entry.moved
                    ? 'bg-blue-100 text-blue-900'
                    : 'bg-muted text-muted-foreground',
                )}
                data-moved={entry.kind === 'page' && entry.moved ? 'true' : undefined}
              >
                {entry.kind === 'page' ? shortName(entry.page) : text.more}
              </li>
            </Fragment>
          ))}
        </ol>
      </section>

      {mutation.isError ? <ErrorAlert message={describePageError(mutation.error)} /> : null}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone}>
          {MESSAGES.common.cancel}
        </Button>
        <Button type="submit" disabled={mutation.isPending}>
          <ArrowDownUpIcon />
          {mutation.isPending ? text.submitting : text.submit(side, shortName(anchor))}
        </Button>
      </DialogFooter>
    </form>
  );
}

export function MovePagesDialog({
  projectId,
  pages,
  thumbnails,
  target,
  onClose,
}: {
  projectId: string;
  pages: readonly PageSchema[];
  /**
   * The thumbnail of each page by its identifier, for a screen that draws the pages at one stage and not as the latest
   * result of the book, which the pages carry. A page without an entry has no picture. Absent for the pages' own.
   */
  thumbnails?: ReadonlyMap<string, string>;
  /** What to move, or null while the dialog is closed. */
  target: MoveTarget | null;
  onClose: () => void;
}): React.JSX.Element {
  const title =
    target?.sourceName === undefined
      ? MESSAGES.order.move.title(target?.pageIds.length ?? 0)
      : MESSAGES.order.move.sourceTitle(target.sourceName);
  return (
    <Dialog open={target !== null} onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            {MESSAGES.order.move.description(target?.pageIds.length ?? 0)}
          </DialogDescription>
        </DialogHeader>
        {target === null ? null : (
          <MoveForm
            projectId={projectId}
            pages={pages}
            thumbnails={thumbnails}
            target={target}
            onDone={onClose}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}
