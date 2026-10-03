import Form from '@rjsf/shadcn';
import validator from '@rjsf/validator-ajv8';
import { useMemo } from 'react';
import type { ProcessorSchema } from '@/api';
import { BoundedNumberWidget } from '@/features/processing/BoundedNumberWidget';
import {
  BOUNDED_NUMBER_WIDGET,
  formSchemaOf,
  hasSettings,
  uiSchemaOf,
} from '@/features/processing/schema';
import { MESSAGES } from '@/shared/messages';

/**
 * The settings of one step, a form drawn from the JSON Schema of its processor.
 *
 * The label of a field is its `title` in the schema and the hint under it its `description`, so a processor that is
 * installed later gets its form without a line of the interface being written. A number with both bounds is a slider
 * with an input, and the form checks every change against the schema as it is made. A field the open page changes for
 * itself says so in its label.
 */

const WIDGETS = { [BOUNDED_NUMBER_WIDGET]: BoundedNumberWidget };

export function ParamsForm({
  processor,
  params,
  marked,
  onChange,
}: {
  processor: ProcessorSchema;
  params: Readonly<Record<string, unknown>>;
  /** The names of the fields the open page changes for itself, which the form marks. */
  marked?: ReadonlySet<string>;
  onChange: (params: Record<string, unknown>) => void;
}): React.JSX.Element {
  const schema = useMemo(() => formSchemaOf(processor.parameters), [processor.parameters]);
  const uiSchema = useMemo(
    () =>
      uiSchemaOf(schema, marked, (title) =>
        MESSAGES.processing.steps.pageSettings.label(
          title,
          MESSAGES.processing.steps.pageSettings.changedMark,
        ),
      ),
    [schema, marked],
  );

  if (!hasSettings(schema)) {
    return <p className="text-sm text-muted-foreground">{MESSAGES.processing.steps.noSettings}</p>;
  }
  return (
    <Form
      schema={schema}
      uiSchema={uiSchema}
      validator={validator}
      widgets={WIDGETS}
      formData={params}
      liveValidate
      showErrorList={false}
      noHtml5Validate
      className="grid gap-3"
      onChange={(event) => onChange({ ...(event.formData ?? {}) })}
    />
  );
}
