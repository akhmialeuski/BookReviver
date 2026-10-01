import { Trash2Icon } from 'lucide-react';
import { useState } from 'react';
import type { PageKind, PageSchema } from '@/api';
import { AttachScanView } from '@/features/pages/AttachScanView';
import { useDeletePage, useUpdatePage } from '@/features/pages/actions';
import { describePageError } from '@/features/pages/errors';
import { asPageKind, PAGE_KINDS } from '@/features/pages/kinds';
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
import { TextareaField } from '@/shared/ui/textarea-field';

/**
 * The dialog that edits one page: its printed number, kind, inclusion in the book and notes, and for a placeholder
 * the binding of a scan, and for any page its deletion after a confirmation.
 *
 * The three steps are one dialog that swaps its content, so the reader stays on one page throughout and the keyboard
 * focus never leaves it. The form starts from the page as it is when the dialog opens, and is not re-read while it is
 * open, so an event that refreshes the strip does not overwrite what is being typed.
 */

const View = {
  Edit: 'edit',
  Attach: 'attach',
  Remove: 'remove',
} as const;

type View = (typeof View)[keyof typeof View];

function EditForm({
  projectId,
  page,
  name,
  onView,
  onDone,
}: {
  projectId: string;
  page: PageSchema;
  name: string;
  onView: (view: View) => void;
  onDone: () => void;
}): React.JSX.Element {
  const update = useUpdatePage(projectId);
  const [label, setLabel] = useState(page.label);
  const [kind, setKind] = useState<PageKind>(page.kind);
  const [included, setIncluded] = useState(page.included);
  const [notes, setNotes] = useState(page.notes);

  return (
    <form
      className="grid gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        // A null clears a label or notes to the empty text, which is what an emptied field means
        update.mutate(
          {
            path: { project_id: projectId, page_id: page.id },
            body: {
              label: label.trim() === '' ? null : label.trim(),
              kind,
              included,
              notes: notes.trim() === '' ? null : notes,
            },
          },
          { onSuccess: onDone },
        );
      }}
    >
      <TextField
        label={MESSAGES.pages.edit.label}
        hint={MESSAGES.pages.edit.labelHint}
        value={label}
        onChange={(event) => setLabel(event.target.value)}
      />
      <SelectField
        label={MESSAGES.pages.edit.kind}
        value={kind}
        onChange={(event) => {
          setKind(asPageKind(event.target.value) ?? kind);
        }}
      >
        {PAGE_KINDS.map((option) => (
          <option key={option} value={option}>
            {MESSAGES.pages.kinds[option]}
          </option>
        ))}
      </SelectField>
      <CheckboxField
        label={MESSAGES.pages.edit.included}
        checked={included}
        onChange={(event) => setIncluded(event.target.checked)}
      />
      <TextareaField
        label={MESSAGES.pages.edit.notes}
        value={notes}
        onChange={(event) => setNotes(event.target.value)}
      />
      {page.origin === 'placeholder' ? (
        <div className="flex flex-wrap items-center gap-3 rounded-md border p-3">
          <span className="text-sm text-muted-foreground">{MESSAGES.pages.edit.noScan}</span>
          <Button type="button" variant="outline" size="sm" onClick={() => onView(View.Attach)}>
            {MESSAGES.pages.edit.attach}
          </Button>
        </div>
      ) : null}
      {update.isError ? <ErrorAlert message={describePageError(update.error)} /> : null}
      <DialogFooter className="sm:justify-between">
        <Button
          type="button"
          variant="ghost"
          className="text-destructive"
          onClick={() => onView(View.Remove)}
          aria-label={`${MESSAGES.pages.edit.remove}: ${name}`}
        >
          <Trash2Icon />
          {MESSAGES.pages.edit.remove}
        </Button>
        <div className="flex flex-col-reverse gap-2 sm:flex-row">
          <Button type="button" variant="outline" onClick={onDone}>
            {MESSAGES.common.cancel}
          </Button>
          <Button type="submit" disabled={update.isPending}>
            {update.isPending ? MESSAGES.pages.edit.saving : MESSAGES.pages.edit.save}
          </Button>
        </div>
      </DialogFooter>
    </form>
  );
}

function RemoveStep({
  projectId,
  page,
  onBack,
  onDone,
}: {
  projectId: string;
  page: PageSchema;
  onBack: () => void;
  onDone: () => void;
}): React.JSX.Element {
  const remove = useDeletePage(projectId);
  return (
    <div className="grid gap-4">
      {remove.isError ? <ErrorAlert message={describePageError(remove.error)} /> : null}
      <DialogFooter>
        <Button variant="outline" onClick={onBack}>
          {MESSAGES.pages.remove.back}
        </Button>
        <Button
          variant="destructive"
          disabled={remove.isPending}
          onClick={() =>
            remove.mutate(
              { path: { project_id: projectId, page_id: page.id } },
              { onSuccess: onDone },
            )
          }
        >
          {remove.isPending ? MESSAGES.pages.remove.submitting : MESSAGES.pages.remove.submit}
        </Button>
      </DialogFooter>
    </div>
  );
}

export function PageEditDialog({
  projectId,
  page,
  onClose,
}: {
  projectId: string;
  /** The page to edit, or null while the dialog is closed. */
  page: PageSchema | null;
  onClose: () => void;
}): React.JSX.Element {
  const [view, setView] = useState<View>(View.Edit);
  const name = page === null ? '' : MESSAGES.pages.name(page.position + 1, page.label);
  const close = (): void => {
    setView(View.Edit);
    onClose();
  };

  let title: string = MESSAGES.pages.edit.title(name);
  let description: string = MESSAGES.pages.edit.description;
  if (view === View.Attach) {
    title = MESSAGES.pages.attach.title;
    description = MESSAGES.pages.attach.description(name);
  } else if (view === View.Remove) {
    title = MESSAGES.pages.remove.title;
    description = MESSAGES.pages.remove.description(name);
  }

  return (
    <Dialog open={page !== null} onOpenChange={(open) => (open ? undefined : close())}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        {page === null ? null : (
          <>
            {view === View.Edit ? (
              <EditForm
                projectId={projectId}
                page={page}
                name={name}
                onView={setView}
                onDone={close}
              />
            ) : null}
            {view === View.Attach ? (
              <AttachScanView
                projectId={projectId}
                page={page}
                onBack={() => setView(View.Edit)}
                onDone={close}
              />
            ) : null}
            {view === View.Remove ? (
              <RemoveStep
                projectId={projectId}
                page={page}
                onBack={() => setView(View.Edit)}
                onDone={close}
              />
            ) : null}
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
