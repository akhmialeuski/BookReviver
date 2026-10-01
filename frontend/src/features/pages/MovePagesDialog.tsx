import { useState } from 'react';
import type { PageSchema } from '@/api';
import { useMovePages, useMoveSourcePages } from '@/features/pages/actions';
import { describePageError } from '@/features/pages/errors';
import { AnchorSide, anchorBody } from '@/features/pages/order';
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
import { SelectField } from '@/shared/ui/select-field';

/**
 * Asks where a page, a group of pages or all the pages of a source should go, and moves them.
 *
 * The place is a page of the book and a side of it. The pages that move are never offered as that page, since a
 * place next to oneself is not defined and the server would refuse it. A group keeps the order it has in the book.
 * A source is moved with one request, which puts a missing part of the book, such as a cover file, in its place.
 */

export interface MoveTarget {
  /** Ids of the pages to move, or of the pages of the source when `sourceId` is set. */
  pageIds: readonly string[];
  /** Set to move every page of a source with the one request made for it. */
  sourceId?: string;
  /** What the dialog calls what moves, such as the file name of a source. */
  sourceName?: string;
}

function MoveForm({
  projectId,
  pages,
  target,
  onDone,
}: {
  projectId: string;
  pages: readonly PageSchema[];
  target: MoveTarget;
  onDone: () => void;
}): React.JSX.Element {
  const movePages = useMovePages(projectId);
  const moveSource = useMoveSourcePages(projectId);
  const moving = new Set(target.pageIds);
  const candidates = pages.filter((page) => !moving.has(page.id));
  const [anchorId, setAnchorId] = useState(candidates[0]?.id ?? '');
  const [side, setSide] = useState<AnchorSide>(AnchorSide.After);
  const mutation = target.sourceId === undefined ? movePages : moveSource;

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

  return (
    <form className="grid gap-4" onSubmit={submit}>
      {candidates.length === 0 ? (
        <p className="text-sm text-muted-foreground">{MESSAGES.pages.move.noAnchor}</p>
      ) : (
        <>
          <SelectField
            label={MESSAGES.pages.move.anchor}
            value={anchorId}
            onChange={(event) => setAnchorId(event.target.value)}
          >
            {candidates.map((page) => (
              <option key={page.id} value={page.id}>
                {[
                  MESSAGES.pages.position(page.position + 1),
                  page.label,
                  MESSAGES.pages.kinds[page.kind],
                ]
                  .filter((part) => part !== '')
                  .join(' · ')}
              </option>
            ))}
          </SelectField>
          <SelectField
            label={MESSAGES.pages.move.side}
            value={side}
            onChange={(event) =>
              setSide(
                event.target.value === AnchorSide.Before ? AnchorSide.Before : AnchorSide.After,
              )
            }
          >
            <option value={AnchorSide.Before}>{MESSAGES.pages.move.before}</option>
            <option value={AnchorSide.After}>{MESSAGES.pages.move.after}</option>
          </SelectField>
        </>
      )}
      {mutation.isError ? <ErrorAlert message={describePageError(mutation.error)} /> : null}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone}>
          {MESSAGES.common.cancel}
        </Button>
        <Button type="submit" disabled={mutation.isPending || candidates.length === 0}>
          {mutation.isPending ? MESSAGES.pages.move.submitting : MESSAGES.pages.move.submit}
        </Button>
      </DialogFooter>
    </form>
  );
}

export function MovePagesDialog({
  projectId,
  pages,
  target,
  onClose,
}: {
  projectId: string;
  pages: readonly PageSchema[];
  /** What to move, or null while the dialog is closed. */
  target: MoveTarget | null;
  onClose: () => void;
}): React.JSX.Element {
  const title =
    target?.sourceName === undefined
      ? MESSAGES.pages.move.title(target?.pageIds.length ?? 0)
      : MESSAGES.pages.move.sourceTitle(target.sourceName);
  return (
    <Dialog open={target !== null} onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{MESSAGES.pages.move.description}</DialogDescription>
        </DialogHeader>
        {target === null ? null : (
          <MoveForm projectId={projectId} pages={pages} target={target} onDone={onClose} />
        )}
      </DialogContent>
    </Dialog>
  );
}
