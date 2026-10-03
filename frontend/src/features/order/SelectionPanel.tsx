import {
  ArrowDownUpIcon,
  HashIcon,
  ImagePlusIcon,
  PlusIcon,
  TriangleAlertIcon,
  XIcon,
} from 'lucide-react';
import { useState } from 'react';
import type { PageKind, PageSchema, PageUpdate } from '@/api';
import { BlankImageBlock } from '@/features/order/BlankImageBlock';
import { InsertMenu } from '@/features/order/InsertMenu';
import type { InsertSpec } from '@/features/order/insert';
import { leafPages, leavesLost } from '@/features/order/leaf';
import type { PlaceToCheck } from '@/features/order/places';
import { commonOf, spanOf } from '@/features/order/summary';
import { useUpdatePages } from '@/features/pages/actions';
import { describePageError } from '@/features/pages/errors';
import { formatNumber, type LabelGap } from '@/features/pages/gaps';
import { asPageKind, PAGE_KINDS } from '@/features/pages/kinds';
import { shortName } from '@/features/pages/names';
import { PageThumbnail } from '@/features/pages/PageThumbnail';
import { StagePanel } from '@/features/workspace/StagePanel';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { CheckboxField } from '@/shared/ui/checkbox-field';
import { ErrorAlert } from '@/shared/ui/error-alert';
import { SelectField } from '@/shared/ui/select-field';
import { TextField } from '@/shared/ui/text-field';
import { TextareaField } from '@/shared/ui/textarea-field';

/**
 * The panel of the Order stage while no numbering is open: what the selected pages are, whether they are part of the
 * book, their notes, and the actions on them, with the places of the book that ask for a look at the foot.
 *
 * Kind, inclusion and notes are written to every selected page at once, and a field that the pages disagree on says
 * so instead of showing one page's value. The printed number of a single page is edited here too, since a number
 * written by hand is the only way to number one page alone. Blank pages cut from a scan get the choice of their image
 * under the kind, and a change of kind that takes a leaf away says that the scan is back. The words for the places to check name the jumps in the
 * numbers and the pages that wait for a scan, and a button walks the grid from one to the next.
 */

const THUMBNAILS_SHOWN = 6;

/** A text field that writes its value when the reader leaves it, and only when it was typed in. */
function BlurField({
  label,
  hint,
  placeholder,
  value,
  mixed,
  multiline,
  onSave,
}: {
  label: string;
  hint?: string;
  placeholder?: string;
  /** The value the pages have, which the field starts from. */
  value: string;
  /** Whether the pages disagree, which starts the field empty and makes an untouched field mean "no change". */
  mixed: boolean;
  multiline: boolean;
  onSave: (value: string) => void;
}): React.JSX.Element {
  const [text, setText] = useState(mixed ? '' : value);
  const [touched, setTouched] = useState(false);
  const field = {
    label,
    placeholder,
    value: text,
    onBlur: () => {
      if (touched && text !== value) {
        onSave(text);
      }
      setTouched(false);
    },
  };
  const change = (next: string): void => {
    setText(next);
    setTouched(true);
  };
  return multiline ? (
    <TextareaField {...field} onChange={(event) => change(event.target.value)} />
  ) : (
    <TextField {...field} hint={hint} onChange={(event) => change(event.target.value)} />
  );
}

export function SelectionPanel({
  projectId,
  selected,
  gaps,
  missing,
  places,
  blankPages,
  onClear,
  onMove,
  onNumber,
  onInsert,
  onAttach,
  onDelete,
  onShowPlace,
}: {
  projectId: string;
  /** The selected pages in book order. */
  selected: readonly PageSchema[];
  gaps: readonly LabelGap[];
  /** The pages of the book that wait for a scan. */
  missing: readonly PageSchema[];
  places: readonly PlaceToCheck[];
  /** Every blank page of the book cut from a scan, which the choice of the image may be applied to together. */
  blankPages: readonly PageSchema[];
  onClear: () => void;
  onMove: () => void;
  onNumber: () => void;
  onInsert: (spec: InsertSpec) => void;
  onAttach: (page: PageSchema) => void;
  onDelete: () => void;
  /** Called with the index into `places` of the one to bring into view. */
  onShowPlace: (index: number) => void;
}): React.JSX.Element {
  const text = MESSAGES.order.panel;
  const update = useUpdatePages(projectId);
  const [shown, setShown] = useState(-1);
  // The changes made and not yet settled, which the controls show at once, since a control that waits for the cache
  // to be read back jumps to its old value for a moment
  const [pending, setPending] = useState<PageUpdate>({});
  // What a change of kind took away, said under the kind of the pages it was made on
  const [notice, setNotice] = useState<{ ids: string; message: string } | null>(null);
  const ids = selected.map((page) => page.id);
  const kind = pending.kind ?? commonOf(selected.map((page) => page.kind));
  const included = pending.included ?? commonOf(selected.map((page) => page.included));
  const notes = commonOf(selected.map((page) => page.notes));
  const only = selected.length === 1 ? selected[0] : undefined;
  const save = (changes: PageUpdate): void => {
    const lost = changes.kind === undefined ? 0 : leavesLost(selected, changes.kind);
    setNotice(lost === 0 ? null : { ids: ids.join(','), message: text.leaf.replaced(lost) });
    setPending((current) => ({ ...current, ...changes }));
    update.mutate({ pageIds: ids, changes }, { onSettled: () => setPending({}) });
  };

  // One sentence for the jumps in the numbers and one for the pages that wait for a scan
  const sentences: string[] = [];
  const [onlyGap] = gaps;
  if (onlyGap !== undefined && gaps.length === 1) {
    const from = formatNumber(onlyGap.style, onlyGap.jumpFrom);
    sentences.push(text.places.jump(from, formatNumber(onlyGap.style, onlyGap.jumpTo)));
  } else if (gaps.length > 1) {
    sentences.push(text.places.jumps(gaps.length));
  }
  const [onlyMissing] = missing;
  if (onlyMissing !== undefined && missing.length === 1) {
    sentences.push(text.places.waiting(shortName(onlyMissing)));
  } else if (missing.length > 1) {
    sentences.push(text.places.waitingMany(missing.length));
  }

  const placesBox =
    places.length === 0 ? null : (
      <div
        className="grid gap-2 rounded-md border border-status-attention bg-status-attention/10 p-3 text-sm"
        data-testid="places-to-check"
      >
        <p className="flex items-start gap-2">
          <TriangleAlertIcon
            className="mt-0.5 size-4 shrink-0 text-status-attention"
            aria-hidden="true"
          />
          <span>
            <strong>{text.places.title(places.length)}</strong> {sentences.join(' ')}
          </span>
        </p>
        <Button
          variant="outline"
          size="sm"
          className="justify-self-start"
          onClick={() => {
            const next = (shown + 1) % places.length;
            setShown(next);
            onShowPlace(next);
          }}
        >
          {shown < 0 ? text.places.show : text.places.showNext}
        </Button>
      </div>
    );

  if (selected.length === 0) {
    return (
      <StagePanel stage="page-order" available>
        <div className="grid gap-4">
          <p className="text-sm text-muted-foreground">{text.nothing}</p>
          {placesBox}
        </div>
      </StagePanel>
    );
  }

  return (
    <StagePanel stage="page-order" available>
      <div className="grid gap-4" data-testid="selection-panel">
        <div className="flex items-center justify-between gap-2">
          <h3
            className="text-xs font-medium tracking-wide text-muted-foreground uppercase"
            data-testid="selection-heading"
          >
            {text.selected(selected.length, spanOf(selected))}
          </h3>
          <Button variant="ghost" size="sm" onClick={onClear}>
            {MESSAGES.workspace.grid.clear}
          </Button>
        </div>
        <ul className="flex flex-wrap gap-1">
          {selected.slice(0, THUMBNAILS_SHOWN).map((page) => (
            <li key={page.id} className="w-14">
              <PageThumbnail page={page} alt="" />
            </li>
          ))}
          {selected.length > THUMBNAILS_SHOWN ? (
            <li className="flex w-14 items-center justify-center text-sm text-muted-foreground">
              {text.more(selected.length - THUMBNAILS_SHOWN)}
            </li>
          ) : null}
        </ul>

        <SelectField
          label={text.kind}
          value={kind ?? ''}
          onChange={(event) => {
            const chosen: PageKind | undefined = asPageKind(event.target.value);
            if (chosen !== undefined) {
              save({ kind: chosen });
            }
          }}
        >
          {kind === null ? (
            <option value="" disabled>
              {text.mixedKind}
            </option>
          ) : null}
          {PAGE_KINDS.map((option) => (
            <option key={option} value={option}>
              {MESSAGES.pages.kinds[option]}
            </option>
          ))}
        </SelectField>

        {notice?.ids === ids.join(',') ? (
          <p
            className="rounded-md border border-status-attention bg-status-attention/10 p-2 text-sm"
            role="status"
            data-testid="leaf-replaced"
          >
            {notice.message}
          </p>
        ) : null}

        {leafPages(selected).length === selected.length ? (
          <BlankImageBlock projectId={projectId} selected={selected} blankPages={blankPages} />
        ) : null}

        <div className="grid gap-1">
          <CheckboxField
            label={text.included}
            checked={included === true}
            onChange={(event) => save({ included: event.target.checked })}
          />
          <p className="text-xs text-muted-foreground">
            {included === null ? text.includedMixed : text.includedHint}
          </p>
        </div>

        {only === undefined ? null : (
          <BlurField
            key={`label-${only.id}-${only.label}`}
            label={text.label}
            hint={text.labelHint}
            value={only.label}
            mixed={false}
            multiline={false}
            onSave={(value) => save({ label: value.trim() === '' ? null : value.trim() })}
          />
        )}

        <BlurField
          key={`notes-${ids.join(',')}-${notes ?? ''}`}
          label={text.notes}
          placeholder={notes === null ? text.notesMixed : undefined}
          value={notes ?? ''}
          mixed={notes === null}
          multiline
          onSave={(value) => save({ notes: value === '' ? null : value })}
        />

        {update.isError ? <ErrorAlert message={describePageError(update.error)} /> : null}

        <section className="grid gap-2" aria-label={text.actions}>
          <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
            {text.actions}
          </h3>
          <Button variant="outline" className="justify-start" onClick={onMove}>
            <ArrowDownUpIcon />
            {text.move}
          </Button>
          <Button variant="outline" className="justify-start" onClick={onNumber}>
            <HashIcon />
            {text.number}
          </Button>
          <InsertMenu hasSelection onInsert={onInsert}>
            <Button variant="outline" className="justify-start">
              <PlusIcon />
              {text.insert}
            </Button>
          </InsertMenu>
          {only?.origin === 'placeholder' ? (
            <Button variant="outline" className="justify-start" onClick={() => onAttach(only)}>
              <ImagePlusIcon />
              {text.attach}
            </Button>
          ) : null}
          <Button
            variant="outline"
            className="justify-start border-destructive/40 text-destructive"
            onClick={onDelete}
          >
            <XIcon />
            {text.delete(selected.length)}
          </Button>
        </section>

        {placesBox}
      </div>
    </StagePanel>
  );
}
