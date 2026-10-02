import type { ImagePolicy } from '@/api';
import { EnumSelect } from '@/features/about/EnumSelect';
import {
  type EditableFields,
  HEIGHT_CM_MAX,
  HEIGHT_CM_MIN,
  type Problem,
  type Problems,
  parseHeight,
  type TextKey,
} from '@/features/about/fields';
import { ContributorsField, IdentifiersField, TextListField } from '@/features/about/ListFields';
import { type Section, sectionId } from '@/features/about/sections';
import type { EditOptions } from '@/features/about/useAutosave';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';
import { Card, CardContent, CardHeader } from '@/shared/ui/card';
import { TextField } from '@/shared/ui/text-field';
import { TextareaField } from '@/shared/ui/textarea-field';

/**
 * The sections of the description of a book, one card each, with the image policy of the book last.
 *
 * Every input shows the value of its field and reports a change at once, and the autosave decides when it is sent.
 * A choice made with a click is sent without waiting, because there is no more typing to wait for. A field whose
 * value is not sent says why under its input.
 */

const IMAGE_POLICIES: readonly ImagePolicy[] = ['compact', 'lossless'];

interface FormProps {
  fields: EditableFields;
  problems: Problems;
  onEdit: (changes: Partial<EditableFields>, options?: EditOptions) => void;
}

function SectionCard({
  section,
  children,
}: {
  section: Section;
  children: React.ReactNode;
}): React.JSX.Element {
  const id = sectionId(section);
  return (
    <Card id={id} aria-labelledby={`${id}-title`} className="scroll-mt-4">
      <CardHeader>
        <h2 id={`${id}-title`} className="font-semibold">
          {MESSAGES.about.sections[section]}
        </h2>
      </CardHeader>
      <CardContent className="grid gap-4 sm:grid-cols-2">{children}</CardContent>
    </Card>
  );
}

function FieldProblem({ problem }: { problem: Problem | undefined }): React.JSX.Element | null {
  return problem === undefined ? null : (
    <p className="text-xs text-destructive">{MESSAGES.about.problems[problem]}</p>
  );
}

export function DescriptionForm({ fields, problems, onEdit }: FormProps): React.JSX.Element {
  const labels = MESSAGES.about.fields;
  const hints = MESSAGES.about.hints;
  const text = (key: TextKey, label: string, hint?: string): React.JSX.Element => (
    <TextField
      label={label}
      hint={hint}
      value={fields[key]}
      onChange={(event) => onEdit({ [key]: event.target.value })}
    />
  );

  return (
    <>
      <SectionCard section="title">
        <div className="grid gap-1 sm:col-span-2">
          <TextField
            label={labels.title}
            value={fields.title}
            aria-invalid={problems.title !== undefined}
            onChange={(event) => onEdit({ title: event.target.value })}
          />
          <FieldProblem problem={problems.title} />
        </div>
        {text('subtitle', labels.subtitle)}
        {text('original_title', labels.originalTitle)}
        <TextListField
          label={labels.parallelTitles}
          hint={hints.parallelTitles}
          value={fields.parallel_titles}
          multiline
          invalid={false}
          onChange={(parallel_titles) => onEdit({ parallel_titles })}
        />
        <div className="grid content-start gap-1">
          <TextListField
            label={labels.languages}
            hint={hints.languages}
            value={fields.languages}
            multiline={false}
            invalid={problems.languages !== undefined}
            onChange={(languages) => onEdit({ languages })}
          />
          <FieldProblem problem={problems.languages} />
        </div>
        <div className="grid gap-1 sm:col-span-2">
          <ContributorsField
            value={fields.contributors}
            onChange={(contributors) => onEdit({ contributors })}
          />
          <FieldProblem problem={problems.contributors} />
        </div>
        <EnumSelect
          label={labels.orthography}
          value={fields.orthography}
          options={MESSAGES.book.orthography}
          onChange={(orthography) => onEdit({ orthography }, { immediate: true })}
        />
        <EnumSelect
          label={labels.script}
          value={fields.script}
          options={MESSAGES.book.script}
          onChange={(script) => onEdit({ script }, { immediate: true })}
        />
      </SectionCard>

      <SectionCard section="publication">
        {text('publication_place', labels.place)}
        {text('publisher', labels.publisher)}
        {text('printer', labels.printer)}
        {text('publication_year', labels.year, hints.year)}
        {text('edition', labels.edition)}
        {text('censorship', labels.censorship, hints.censorship)}
        {text('series', labels.series)}
        {text('series_number', labels.seriesNumber)}
        {text('volume', labels.volume)}
      </SectionCard>

      <SectionCard section="copy">
        {text('printed_pagination', labels.printedPagination, hints.printedPagination)}
        <div className="grid content-start gap-1">
          <TextField
            label={labels.height}
            type="number"
            min={HEIGHT_CM_MIN}
            max={HEIGHT_CM_MAX}
            value={fields.height_cm ?? ''}
            aria-invalid={problems.height_cm !== undefined}
            onChange={(event) => onEdit({ height_cm: parseHeight(event.target.value) })}
          />
          <FieldProblem problem={problems.height_cm} />
        </div>
        {text('illustrations', labels.illustrations)}
        {text('binding', labels.binding)}
        {text('copy_holder', labels.copyHolder)}
        {text('copy_notes', labels.copyNotes)}
        <div className="grid gap-1 sm:col-span-2">
          <IdentifiersField
            value={fields.identifiers}
            onChange={(identifiers) => onEdit({ identifiers })}
          />
          <FieldProblem problem={problems.identifiers} />
        </div>
      </SectionCard>

      <SectionCard section="subjects">
        <TextListField
          label={labels.subjects}
          hint={hints.subjects}
          value={fields.subjects}
          multiline
          invalid={false}
          onChange={(subjects) => onEdit({ subjects })}
        />
        <EnumSelect
          label={labels.rights}
          value={fields.rights}
          options={MESSAGES.about.rights}
          onChange={(rights) => onEdit({ rights }, { immediate: true })}
        />
      </SectionCard>

      <SectionCard section="notes">
        <div className="sm:col-span-2">
          <TextareaField
            label={labels.notes}
            className="min-h-32"
            value={fields.notes}
            onChange={(event) => onEdit({ notes: event.target.value })}
          />
        </div>
      </SectionCard>

      <SectionCard section="storage">
        <fieldset className="grid gap-3 sm:col-span-2">
          <legend className="mb-2 text-sm font-medium">{MESSAGES.about.imagePolicy.legend}</legend>
          {IMAGE_POLICIES.map((policy) => {
            const option = MESSAGES.about.imagePolicy.options[policy];
            return (
              <label
                key={policy}
                className={cn(
                  'flex cursor-pointer items-start gap-3 rounded-md border p-3 text-sm',
                  fields.image_policy === policy ? 'border-primary bg-accent' : '',
                )}
              >
                <input
                  type="radio"
                  name="image-policy"
                  className="mt-1"
                  checked={fields.image_policy === policy}
                  onChange={() => onEdit({ image_policy: policy }, { immediate: true })}
                />
                <span className="grid gap-0.5">
                  <span className="font-medium">{option.label}</span>
                  <span className="text-muted-foreground">{option.description}</span>
                </span>
              </label>
            );
          })}
          <p className="text-xs text-muted-foreground">{MESSAGES.about.imagePolicy.note}</p>
        </fieldset>
      </SectionCard>
    </>
  );
}
