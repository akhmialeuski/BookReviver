import Form, { generateTemplates } from '@rjsf/shadcn';
import type { FieldTemplateProps, RJSFSchema } from '@rjsf/utils';
import validator from '@rjsf/validator-ajv8';
import { PlusIcon, XIcon } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { FORM_WIDGETS } from '@/features/processing/BoundedNumberWidget';
import {
  chipsOf,
  choicesOf,
  type PageValues,
  showValue,
  type ValueChip,
  type ValueChoice,
} from '@/features/processing/pageSettings';
import { useRemoveValue, useSetValue } from '@/features/processing/queries';
import { fieldSchemaOf, uiSchemaOf } from '@/features/processing/schema';
import { useDebouncedCallback } from '@/shared/hooks/useDebouncedCallback';
import { describeError } from '@/shared/http/problem';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The values a setting has for a part of the pages, drawn under the setting in the form of its step.
 *
 * Each value is a chip: the odd pages, the even pages, a group, or the open page, which is drawn apart. A click on a chip
 * opens the field again to change the value, and the cross takes it back, so the pages use the next value by strength.
 * The menu that adds one lists the open page, the selected pages, the odd pages, the even pages and the groups, each
 * with the pages it covers. A part that has a value of the setting already is not offered again.
 */

const labels = MESSAGES.processing.steps.values;

/** How long after the last change of a value in its field the value is sent. */
const SEND_AFTER_MS = 400;

/** The open page and the saved step the values of a form are shown for. */
export interface StepFormValues {
  page: PageValues;
  stepId: string;
}

/** What the form hands its field templates: the values to show, and the schema the fields of the form are drawn from. */
interface FormContext {
  values?: StepFormValues;
  schema: RJSFSchema;
}

const THEME_FIELD_TEMPLATE = generateTemplates().FieldTemplate;
if (THEME_FIELD_TEMPLATE === undefined) {
  throw new Error('The theme of the forms has no template of a field to draw the values under.');
}
const DefaultFieldTemplate = THEME_FIELD_TEMPLATE;

/**
 * The template of a field of a step: the field the theme draws, with the values the parts of the pages have for it
 * under its widget.
 */
export function ValuesFieldTemplate(props: FieldTemplateProps): React.JSX.Element {
  const { values, schema } = props.registry.formContext as FormContext;
  const path = props.fieldPathId.path;
  const name = path.length === 1 && typeof path[0] === 'string' ? path[0] : undefined;
  // Only a field that is drawn and holds one value has values of its own: not the form itself, nor a hidden method name
  if (
    values === undefined ||
    name === undefined ||
    props.hidden ||
    props.schema.type === 'object'
  ) {
    return <DefaultFieldTemplate {...props} />;
  }
  return (
    <DefaultFieldTemplate {...props}>
      <div className="grid gap-2">
        {props.children}
        <FieldValues
          values={values}
          schema={schema}
          name={name}
          title={props.label}
          current={props.formData ?? props.schema.default}
        />
      </div>
    </DefaultFieldTemplate>
  );
}

/**
 * The chips of one setting and the menu that adds one, with the field that changes the value of a chip open under them.
 *
 * The value typed in the field is sent a moment after the last change, so a drag of a slider is one request. The send is
 * kept here and not in the field, so that taking the value back can drop it, and it holds the whole request.
 */
function FieldValues({
  values,
  schema,
  name,
  title,
  current,
}: {
  values: StepFormValues;
  schema: RJSFSchema;
  name: string;
  title: string;
  /** The value of the setting in the recipe, which a new value starts from. */
  current: unknown;
}): React.JSX.Element {
  const { page, stepId } = values;
  const { projectId, stage } = page;
  const set = useSetValue(projectId, stage);
  const remove = useRemoveValue(projectId, stage);
  const [editing, setEditing] = useState<string | null>(null);
  const chips = chipsOf(page, stepId, name);
  const choices = choicesOf(page, stepId, name);
  const path = { project_id: projectId, stage, step_id: stepId, name };
  const edited = chips.find((chip) => chip.id === editing);
  const failure = set.error ?? remove.error;
  // The value waiting to be sent carries the whole request, since it is made after the page may have turned
  const send = useDebouncedCallback(set.mutate, SEND_AFTER_MS, { flushOnUnmount: true });
  // The chips of the setting have the same ids on every page, so the editor belongs to the page as well as to the chip.
  // A value still waiting is sent to its own page before another chip or page takes the editor
  const editedKey = edited === undefined ? null : `${page.page.id}|${edited.id}`;
  useEffect(() => (editedKey === null ? undefined : () => send.flush()), [editedKey, send]);

  const add = (choice: ValueChoice): void => {
    set.mutate({ path, body: { ...choice.target, value: current } });
    setEditing(choice.id);
  };

  return (
    <div className="grid gap-2" data-testid="field-values" data-field={name}>
      <div className="flex flex-wrap items-center gap-1.5">
        {chips.map((chip) => (
          <Chip
            key={chip.id}
            chip={chip}
            editing={chip.id === editing}
            onEdit={() => setEditing(chip.id === editing ? null : chip.id)}
            onRemove={() => {
              // A value waiting to be sent would set the value that is taken back now
              if (chip.id === editing) {
                send.cancel();
              }
              remove.mutate({ path, query: chip.target });
              setEditing(null);
            }}
          />
        ))}
        <AddValue title={title} choices={choices} onChoose={add} />
      </div>
      {edited === undefined ? null : (
        <ValueEditor
          // Another chip or another page starts from its own value, and not from the one the last was changed to
          key={editedKey}
          schema={schema}
          name={name}
          value={edited.value}
          onSend={(value) => send({ path, body: { ...edited.target, value } })}
        />
      )}
      {failure === null ? null : <ErrorAlert message={describeError(failure)} />}
    </div>
  );
}

function Chip({
  chip,
  editing,
  onEdit,
  onRemove,
}: {
  chip: ValueChip;
  editing: boolean;
  onEdit: () => void;
  onRemove: () => void;
}): React.JSX.Element {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full border text-xs',
        chip.own ? 'border-status-attention bg-status-attention/15' : 'bg-muted',
      )}
      data-testid="value-chip"
      data-scope={chip.target.scope}
      data-own={chip.own}
    >
      <button
        type="button"
        className="rounded-l-full py-0.5 pr-1 pl-2 font-semibold hover:underline"
        aria-expanded={editing}
        aria-label={labels.edit(chip.title)}
        data-testid="value-chip-edit"
        onClick={onEdit}
      >
        {chip.title} <span className="font-normal">{showValue(chip.value)}</span>
      </button>
      <button
        type="button"
        className="rounded-r-full py-0.5 pr-1.5 pl-0.5 text-muted-foreground hover:text-foreground"
        aria-label={labels.remove(chip.title)}
        data-testid="value-chip-remove"
        onClick={onRemove}
      >
        <XIcon className="size-3" aria-hidden="true" />
      </button>
    </span>
  );
}

function AddValue({
  title,
  choices,
  onChoose,
}: {
  title: string;
  choices: ReturnType<typeof choicesOf>;
  onChoose: (choice: ValueChoice) => void;
}): React.JSX.Element {
  const item = (choice: ValueChoice): React.JSX.Element => (
    <DropdownMenuItem
      key={choice.id}
      disabled={choice.taken || choice.pages === 0}
      className="justify-between"
      data-testid={`value-choice-${choice.id}`}
      onSelect={() => onChoose(choice)}
    >
      <span>{choice.title}</span>
      <span className="text-muted-foreground">{choice.pages}</span>
    </DropdownMenuItem>
  );
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="inline-flex items-center gap-0.5 rounded-full border border-dashed px-2 py-0.5 text-xs text-muted-foreground hover:text-foreground"
          aria-label={labels.addFor(title)}
          data-testid="value-add"
        >
          <PlusIcon className="size-3" aria-hidden="true" />
          {labels.add}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuLabel className="text-xs font-normal text-muted-foreground uppercase">
          {labels.menu(title)}
        </DropdownMenuLabel>
        {choices.parts.map(item)}
        <DropdownMenuSeparator />
        <DropdownMenuLabel className="text-xs font-normal text-muted-foreground uppercase">
          {labels.group}
        </DropdownMenuLabel>
        {choices.groups.length === 0 ? (
          <DropdownMenuItem disabled>{labels.noGroups}</DropdownMenuItem>
        ) : (
          choices.groups.map(item)
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/**
 * The field of one setting, drawn the way the form of the step draws it, which changes the value of a chip.
 *
 * Every change the schema accepts is handed to `onSend`, and a value it refuses is not.
 */
function ValueEditor({
  schema,
  name,
  value,
  onSend,
}: {
  schema: RJSFSchema;
  name: string;
  value: unknown;
  onSend: (value: unknown) => void;
}): React.JSX.Element | null {
  const [held, setHeld] = useState(value);
  const field = useMemo(() => {
    const property = fieldSchemaOf(schema, name);
    if (property === undefined) {
      return undefined;
    }
    const alone: RJSFSchema = {
      type: 'object',
      properties: { [name]: property },
      ...(schema.$defs === undefined ? {} : { $defs: schema.$defs }),
    };
    // The title is above the chips already, so the field is drawn without one
    return {
      schema: alone,
      uiSchema: {
        ...uiSchemaOf(alone),
        [name]: { ...uiSchemaOf(alone)[name], 'ui:options': { label: false } },
      },
    };
  }, [schema, name]);
  if (field === undefined) {
    return null;
  }
  return (
    <div data-testid="value-editor">
      <Form
        schema={field.schema}
        uiSchema={field.uiSchema}
        validator={validator}
        widgets={FORM_WIDGETS}
        formData={{ [name]: held }}
        liveValidate
        showErrorList={false}
        noHtml5Validate
        onChange={(event) => {
          const next = (event.formData as Record<string, unknown> | undefined)?.[name];
          setHeld(next);
          if (next !== undefined && event.errors.length === 0) {
            onSend(next);
          }
        }}
      />
    </div>
  );
}
