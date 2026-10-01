import { useState } from 'react';
import type { NewPageOrigin, PageKind, PageSchema } from '@/api';
import { useCreatePage } from '@/features/pages/actions';
import { describePageError } from '@/features/pages/errors';
import { asPageKind, PAGE_KINDS } from '@/features/pages/kinds';
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
import { TextField } from '@/shared/ui/text-field';
import { TextareaField } from '@/shared/ui/textarea-field';

/**
 * Adds a page the scans lack: a placeholder that waits for a scan, or a white blank leaf, with its role, printed
 * number and notes, and a place in the book that is the end unless a page and a side of it are chosen.
 *
 * A blank leaf has the median size of the pages of the book, which the server works out, so no size is asked for. A
 * book with no page that has an image answers a conflict that says so, and the message is shown as it is.
 */

const ORIGINS: readonly NewPageOrigin[] = ['placeholder', 'blank'];
const END_OF_BOOK = '';

function AddForm({
  projectId,
  pages,
  anchorId,
  onDone,
}: {
  projectId: string;
  pages: readonly PageSchema[];
  /** A page to put the new one next to when the dialog opens, or empty for the end of the book. */
  anchorId: string;
  onDone: () => void;
}): React.JSX.Element {
  const create = useCreatePage(projectId);
  const [origin, setOrigin] = useState<NewPageOrigin>('placeholder');
  const [kind, setKind] = useState<PageKind>('text');
  const [label, setLabel] = useState('');
  const [notes, setNotes] = useState('');
  const [anchor, setAnchor] = useState(anchorId);
  const [side, setSide] = useState<AnchorSide>(AnchorSide.After);

  return (
    <form
      className="grid gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        create.mutate(
          {
            path: { project_id: projectId },
            body: {
              origin,
              kind,
              label: label.trim(),
              notes,
              ...(anchor === END_OF_BOOK ? {} : anchorBody({ pageId: anchor, side })),
            },
          },
          { onSuccess: onDone },
        );
      }}
    >
      <SelectField
        label={MESSAGES.pages.add.origin}
        value={origin}
        onChange={(event) => {
          const chosen = ORIGINS.find((candidate) => candidate === event.target.value);
          if (chosen !== undefined) {
            setOrigin(chosen);
          }
        }}
      >
        {ORIGINS.map((option) => (
          <option key={option} value={option}>
            {MESSAGES.pages.origins[option]}
          </option>
        ))}
      </SelectField>
      <SelectField
        label={MESSAGES.pages.add.kind}
        value={kind}
        onChange={(event) => setKind(asPageKind(event.target.value) ?? kind)}
      >
        {PAGE_KINDS.map((option) => (
          <option key={option} value={option}>
            {MESSAGES.pages.kinds[option]}
          </option>
        ))}
      </SelectField>
      <TextField
        label={MESSAGES.pages.add.label}
        value={label}
        onChange={(event) => setLabel(event.target.value)}
      />
      <TextareaField
        label={MESSAGES.pages.add.notes}
        value={notes}
        onChange={(event) => setNotes(event.target.value)}
      />
      <div className="grid gap-4 sm:grid-cols-2">
        <SelectField
          label={MESSAGES.pages.add.place}
          value={anchor}
          onChange={(event) => setAnchor(event.target.value)}
        >
          <option value={END_OF_BOOK}>{MESSAGES.pages.add.atEnd}</option>
          {pages.map((page) => (
            <option key={page.id} value={page.id}>
              {[MESSAGES.pages.position(page.position + 1), page.label]
                .filter((part) => part !== '')
                .join(' · ')}
            </option>
          ))}
        </SelectField>
        <SelectField
          label={MESSAGES.pages.add.anchor}
          value={side}
          disabled={anchor === END_OF_BOOK}
          onChange={(event) =>
            setSide(event.target.value === AnchorSide.Before ? AnchorSide.Before : AnchorSide.After)
          }
        >
          <option value={AnchorSide.Before}>{MESSAGES.pages.add.before}</option>
          <option value={AnchorSide.After}>{MESSAGES.pages.add.after}</option>
        </SelectField>
      </div>
      {create.isError ? <ErrorAlert message={describePageError(create.error)} /> : null}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone}>
          {MESSAGES.common.cancel}
        </Button>
        <Button type="submit" disabled={create.isPending}>
          {create.isPending ? MESSAGES.pages.add.submitting : MESSAGES.pages.add.submit}
        </Button>
      </DialogFooter>
    </form>
  );
}

export function AddPageDialog({
  projectId,
  pages,
  open,
  anchorId = END_OF_BOOK,
  onClose,
}: {
  projectId: string;
  pages: readonly PageSchema[];
  open: boolean;
  anchorId?: string;
  onClose: () => void;
}): React.JSX.Element {
  return (
    <Dialog open={open} onOpenChange={(next) => (next ? undefined : onClose())}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{MESSAGES.pages.add.title}</DialogTitle>
          <DialogDescription>{MESSAGES.pages.add.description}</DialogDescription>
        </DialogHeader>
        {open ? (
          <AddForm projectId={projectId} pages={pages} anchorId={anchorId} onDone={onClose} />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
