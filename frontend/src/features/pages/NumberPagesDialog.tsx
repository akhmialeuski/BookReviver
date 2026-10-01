import { useState } from 'react';
import type { LabelStyle, PageKind, PageSchema } from '@/api';
import { useNumberPages } from '@/features/pages/actions';
import { describePageError } from '@/features/pages/errors';
import { asLabelStyle, LABEL_STYLES, PAGE_KINDS } from '@/features/pages/kinds';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { CheckboxField } from '@/shared/ui/checkbox-field';
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

/**
 * Numbers a range of pages: the first and last page of the range, the style of the numbers, the first number, whether
 * to put them in square brackets, and the kinds of page to leave out, such as the plates of an old book.
 *
 * The server writes the numbers, so the dialog only describes the range. It starts from a plate-free default of
 * Arabic numerals from 1 and the whole book, and offers the kinds as checkboxes because which kinds stand outside
 * the pagination differs from book to book.
 */

const FIRST_NUMBER = 1;
const DEFAULT_SKIPPED: readonly PageKind[] = ['plate'];

function NumberForm({
  projectId,
  pages,
  firstId,
  onDone,
}: {
  projectId: string;
  pages: readonly PageSchema[];
  /** The page the range starts at when the dialog opens. */
  firstId: string;
  onDone: () => void;
}): React.JSX.Element {
  const number = useNumberPages(projectId);
  const [first, setFirst] = useState(firstId);
  const [last, setLast] = useState(pages.at(-1)?.id ?? '');
  const [style, setStyle] = useState<LabelStyle>('arabic');
  const [start, setStart] = useState(String(FIRST_NUMBER));
  const [bracketed, setBracketed] = useState(false);
  const [skipped, setSkipped] = useState<ReadonlySet<PageKind>>(new Set(DEFAULT_SKIPPED));

  const options = pages.map((page) => (
    <option key={page.id} value={page.id}>
      {[MESSAGES.pages.position(page.position + 1), page.label, MESSAGES.pages.kinds[page.kind]]
        .filter((part) => part !== '')
        .join(' · ')}
    </option>
  ));
  const startNumber = Number.parseInt(start, 10);

  return (
    <form
      className="grid gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        number.mutate(
          {
            path: { project_id: projectId },
            body: {
              first_page_id: first,
              last_page_id: last,
              style,
              start: Number.isInteger(startNumber) ? startNumber : FIRST_NUMBER,
              bracketed,
              skip_kinds: [...skipped],
            },
          },
          { onSuccess: onDone },
        );
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <SelectField
          label={MESSAGES.pages.number.first}
          value={first}
          onChange={(event) => setFirst(event.target.value)}
        >
          {options}
        </SelectField>
        <SelectField
          label={MESSAGES.pages.number.last}
          value={last}
          onChange={(event) => setLast(event.target.value)}
        >
          {options}
        </SelectField>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <SelectField
          label={MESSAGES.pages.number.style}
          value={style}
          onChange={(event) => setStyle(asLabelStyle(event.target.value) ?? style)}
        >
          {LABEL_STYLES.map((option) => (
            <option key={option} value={option}>
              {MESSAGES.pages.labelStyles[option]}
            </option>
          ))}
        </SelectField>
        <TextField
          label={MESSAGES.pages.number.start}
          type="number"
          min={FIRST_NUMBER}
          value={start}
          onChange={(event) => setStart(event.target.value)}
          disabled={style === 'none'}
        />
      </div>
      <CheckboxField
        label={MESSAGES.pages.number.bracketed}
        checked={bracketed}
        onChange={(event) => setBracketed(event.target.checked)}
        disabled={style === 'none'}
      />
      <fieldset className="grid gap-2">
        <legend className="mb-1 text-sm font-medium">{MESSAGES.pages.number.skip}</legend>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {PAGE_KINDS.map((kind) => (
            <CheckboxField
              key={kind}
              label={MESSAGES.pages.kinds[kind]}
              checked={skipped.has(kind)}
              onChange={(event) =>
                setSkipped((current) => {
                  const next = new Set(current);
                  if (event.target.checked) {
                    next.add(kind);
                  } else {
                    next.delete(kind);
                  }
                  return next;
                })
              }
            />
          ))}
        </div>
      </fieldset>
      {number.isError ? <ErrorAlert message={describePageError(number.error)} /> : null}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone}>
          {MESSAGES.common.cancel}
        </Button>
        <Button type="submit" disabled={number.isPending || first === '' || last === ''}>
          {number.isPending ? MESSAGES.pages.number.submitting : MESSAGES.pages.number.submit}
        </Button>
      </DialogFooter>
    </form>
  );
}

export function NumberPagesDialog({
  projectId,
  pages,
  open,
  firstId,
  onClose,
}: {
  projectId: string;
  pages: readonly PageSchema[];
  open: boolean;
  /** The page the range starts at, such as the first selected page; the first of the book when omitted. */
  firstId?: string;
  onClose: () => void;
}): React.JSX.Element {
  return (
    <Dialog open={open} onOpenChange={(next) => (next ? undefined : onClose())}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{MESSAGES.pages.number.title}</DialogTitle>
          <DialogDescription>{MESSAGES.pages.number.description}</DialogDescription>
        </DialogHeader>
        {open && pages.length > 0 ? (
          <NumberForm
            projectId={projectId}
            pages={pages}
            firstId={firstId ?? pages[0]?.id ?? ''}
            onDone={onClose}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
