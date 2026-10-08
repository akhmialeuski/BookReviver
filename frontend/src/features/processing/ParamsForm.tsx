import Form from '@rjsf/shadcn';
import validator from '@rjsf/validator-ajv8';
import { useMemo } from 'react';
import type { ProcessorSchema } from '@/api';
import { FORM_WIDGETS } from '@/features/processing/BoundedNumberWidget';
import { type StepFormValues, ValuesFieldTemplate } from '@/features/processing/FieldValues';
import { formSchemaOf, hasSettings, uiSchemaOf } from '@/features/processing/schema';
import { MESSAGES } from '@/shared/messages';

/**
 * The settings of one step, a form drawn from the JSON Schema of its processor.
 *
 * The label of a field is its `title` in the schema and the hint under it its `description`, so a processor that is
 * installed later gets its form without a line of the interface being written. A number with both bounds is a slider
 * with an input, and the form checks every change against the schema as it is made. When the form is given the values
 * of the open page, each setting carries under it the values the parts of the pages have for it.
 */

const TEMPLATES = { FieldTemplate: ValuesFieldTemplate };

export function ParamsForm({
  processor,
  params,
  values,
  idPrefix,
  onChange,
}: {
  processor: ProcessorSchema;
  params: Readonly<Record<string, unknown>>;
  /** The open page and the step, which the values for parts of the pages are shown for, or absent to show none. */
  values?: StepFormValues;
  /** What the ids of the fields start with, so two forms of one step on a screen keep their labels apart. */
  idPrefix?: string;
  onChange: (params: Record<string, unknown>) => void;
}): React.JSX.Element {
  const schema = useMemo(() => formSchemaOf(processor.parameters), [processor.parameters]);
  const uiSchema = useMemo(() => uiSchemaOf(schema), [schema]);
  const formContext = useMemo(() => ({ values, schema }), [values, schema]);

  if (!hasSettings(schema)) {
    return <p className="text-sm text-muted-foreground">{MESSAGES.processing.steps.noSettings}</p>;
  }
  return (
    <Form
      schema={schema}
      uiSchema={uiSchema}
      validator={validator}
      widgets={FORM_WIDGETS}
      templates={TEMPLATES}
      formContext={formContext}
      formData={params}
      idPrefix={idPrefix}
      liveValidate
      showErrorList={false}
      noHtml5Validate
      className="grid gap-3"
      onChange={(event) => onChange({ ...(event.formData ?? {}) })}
    />
  );
}
