import { useState } from 'react';
import type { NumberDisplay, PageKind, PageSchema, PaginationSectionSchema } from '@/api';
import { clampStart } from '@/features/order/numbering';
import { type SectionDraft, sectionBody } from '@/features/order/sectionDraft';
import { useCreateSection, useDeleteSection, usePutSection } from '@/features/pages/actions';
import { describePageError } from '@/features/pages/errors';
import { LABEL_STYLES, PAGE_KINDS } from '@/features/pages/kinds';
import { SegmentedRadio } from '@/features/pages/SegmentedRadio';
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
 * The dialog that makes a pagination section or changes one: the page it starts at, its name, the style and first
 * number of its numbers, a prefix, whether its pages count and print their numbers, and the kinds of page it takes as a
 * sequence of its own.
 *
 * The form only builds the body, and the server checks it and writes the numbers, so a section that cannot be made, such
 * as a second one starting on the same page, comes back as the server's own sentence. A section that exists can also be
 * deleted here, which hands its pages to the section before it.
 */

const DISPLAYS = Object.keys(MESSAGES.order.sections.displays) as NumberDisplay[];

export function SectionDialog({
  projectId,
  pages,
  section,
  initial,
  onClose,
}: {
  projectId: string;
  pages: readonly PageSchema[];
  /** The section to change, or undefined to make a new one. */
  section: PaginationSectionSchema | undefined;
  /** What the form starts with: the section that is changed, or a new section that starts at a page. */
  initial: SectionDraft;
  onClose: () => void;
}): React.JSX.Element {
  const text = MESSAGES.order.sections;
  const form = text.form;
  const [draft, setDraft] = useState<SectionDraft>(initial);
  const create = useCreateSection(projectId);
  const put = usePutSection(projectId);
  const remove = useDeleteSection(projectId);
  const busy = create.isPending || put.isPending || remove.isPending;
  const error = [create, put, remove].find((mutation) => mutation.isError)?.error;
  const noNumbers = draft.style === 'none' || draft.display === 'not-counted';

  const toggleKind = (kind: PageKind, taken: boolean): void =>
    setDraft({
      ...draft,
      kinds: taken ? [...draft.kinds, kind] : draft.kinds.filter((candidate) => candidate !== kind),
    });

  const save = (): void => {
    const body = sectionBody(draft);
    if (section === undefined) {
      create.mutate({ path: { project_id: projectId }, body }, { onSuccess: onClose });
    } else {
      put.mutate(
        { path: { project_id: projectId, section_id: section.id }, body },
        { onSuccess: onClose },
      );
    }
  };

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) {
          onClose();
        }
      }}
    >
      <DialogContent data-testid="section-dialog">
        <DialogHeader>
          <DialogTitle>{section === undefined ? form.addTitle : form.editTitle}</DialogTitle>
          <DialogDescription>{form.description}</DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            save();
          }}
        >
          <TextField
            label={form.name}
            placeholder={form.namePlaceholder}
            value={draft.name}
            onChange={(event) => setDraft({ ...draft, name: event.target.value })}
          />
          <SelectField
            label={form.first}
            value={draft.firstId}
            onChange={(event) => setDraft({ ...draft, firstId: event.target.value })}
          >
            {pages.map((page) => (
              <option key={page.id} value={page.id}>
                {MESSAGES.order.numbering.page(
                  page.position + 1,
                  page.label,
                  MESSAGES.pages.kinds[page.kind],
                )}
              </option>
            ))}
          </SelectField>
          <SelectField
            label={form.display}
            hint={text.displays[draft.display].hint}
            value={draft.display}
            onChange={(event) => {
              const chosen = DISPLAYS.find((display) => display === event.target.value);
              if (chosen !== undefined) {
                setDraft({ ...draft, display: chosen });
              }
            }}
          >
            {DISPLAYS.map((display) => (
              <option key={display} value={display}>
                {text.displays[display].label}
              </option>
            ))}
          </SelectField>
          <SegmentedRadio
            legend={form.style}
            value={draft.style}
            options={LABEL_STYLES.map((style) => ({
              value: style,
              label: MESSAGES.order.numbering.styles[style],
            }))}
            onChange={(style) =>
              setDraft({ ...draft, style, start: clampStart(style, draft.start) })
            }
          />
          <div className="grid grid-cols-2 gap-3">
            <TextField
              label={form.start}
              type="number"
              min={1}
              value={draft.start}
              disabled={noNumbers}
              onChange={(event) =>
                setDraft({ ...draft, start: clampStart(draft.style, event.target.valueAsNumber) })
              }
            />
            <TextField
              label={form.prefix}
              hint={form.prefixHint}
              value={draft.prefix}
              disabled={noNumbers}
              onChange={(event) => setDraft({ ...draft, prefix: event.target.value })}
            />
          </div>
          <fieldset className="grid gap-2">
            <legend className="mb-1 text-sm font-medium">{form.kinds}</legend>
            <div className="grid grid-cols-2 gap-2">
              {PAGE_KINDS.map((kind) => (
                <CheckboxField
                  key={kind}
                  label={MESSAGES.pages.kinds[kind]}
                  checked={draft.kinds.includes(kind)}
                  onChange={(event) => toggleKind(kind, event.target.checked)}
                />
              ))}
            </div>
            <p className="text-xs text-muted-foreground">{form.kindsHint}</p>
          </fieldset>
          {error === undefined ? null : <ErrorAlert message={describePageError(error)} />}
          <DialogFooter className="sm:justify-between">
            {section === undefined ? (
              <span />
            ) : (
              <Button
                type="button"
                variant="outline"
                className="border-destructive/40 text-destructive"
                disabled={busy}
                onClick={() =>
                  remove.mutate(
                    { path: { project_id: projectId, section_id: section.id } },
                    { onSuccess: onClose },
                  )
                }
              >
                {remove.isPending ? form.removing : form.remove}
              </Button>
            )}
            <div className="flex flex-col-reverse gap-2 sm:flex-row">
              <Button type="button" variant="outline" onClick={onClose}>
                {MESSAGES.common.cancel}
              </Button>
              <Button type="submit" disabled={busy}>
                {create.isPending || put.isPending ? form.saving : form.save}
              </Button>
            </div>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
