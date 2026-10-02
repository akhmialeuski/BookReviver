import { PlusIcon, Trash2Icon } from 'lucide-react';
import { useEffect, useState } from 'react';
import type { ContributorSchema, IdentifierSchema } from '@/api';
import { EnumSelect } from '@/features/about/EnumSelect';
import { isEqual, parseCodes, parseLines } from '@/features/about/fields';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { TextField } from '@/shared/ui/text-field';
import { TextareaField } from '@/shared/ui/textarea-field';

/**
 * The fields of the About tab that hold a list: lines of text, language codes, contributors and identifiers.
 *
 * A list is saved as a whole, but it is typed as text or as rows, so each field keeps what the person typed and
 * hands the parsed list up with every change. Without that, a space or a new line at the end of the text would be
 * parsed away the moment it was typed.
 */

const RowKeys = new WeakMap<object, number>();
let nextRowKey = 0;

/** Give a row a key that stays the same while the row is edited, because an edit passes it on to the new row. */
function rowKey(row: object): number {
  let key = RowKeys.get(row);
  if (key === undefined) {
    key = nextRowKey;
    nextRowKey += 1;
    RowKeys.set(row, key);
  }
  return key;
}

function editedRow<T extends object>(row: T, changes: Partial<T>): T {
  const edited = { ...row, ...changes };
  RowKeys.set(edited, rowKey(row));
  return edited;
}

/** A field of text with one entry per line, or of codes separated by spaces. */
export function TextListField({
  label,
  hint,
  value,
  multiline,
  invalid,
  onChange,
}: {
  label: string;
  hint?: string;
  value: readonly string[];
  /** Entries on lines of their own, in a text area, or codes in a single line. */
  multiline: boolean;
  invalid: boolean;
  onChange: (value: string[]) => void;
}): React.JSX.Element {
  const parse = multiline ? parseLines : parseCodes;
  const join = multiline ? '\n' : ' ';
  const [text, setText] = useState(() => value.join(join));

  // Follow a change that did not come from this field, such as a suggestion that was used
  useEffect(() => {
    if (!isEqual(parse(text), value)) {
      setText(value.join(join));
    }
  }, [value, text, parse, join]);

  const change = (next: string): void => {
    setText(next);
    onChange(parse(next));
  };

  return multiline ? (
    <div className="grid gap-1">
      <TextareaField
        label={label}
        value={text}
        aria-invalid={invalid}
        onChange={(event) => change(event.target.value)}
      />
      {hint === undefined ? null : <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  ) : (
    <TextField
      label={label}
      hint={hint}
      value={text}
      aria-invalid={invalid}
      onChange={(event) => change(event.target.value)}
    />
  );
}

/** The people who took part in the making of the book, one row each with the name as printed and the role. */
export function ContributorsField({
  value,
  onChange,
}: {
  value: readonly ContributorSchema[];
  onChange: (value: ContributorSchema[]) => void;
}): React.JSX.Element {
  const messages = MESSAGES.about.contributors;
  const change = (row: ContributorSchema, changes: Partial<ContributorSchema>): void =>
    onChange(value.map((entry) => (entry === row ? editedRow(row, changes) : entry)));
  return (
    <fieldset className="grid gap-3">
      <legend className="mb-2 text-sm font-medium">{MESSAGES.about.fields.contributors}</legend>
      {value.map((row) => (
        <div
          key={rowKey(row)}
          className="grid items-end gap-2 sm:grid-cols-[1fr_14rem_auto]"
          data-testid="contributor"
        >
          <TextField
            label={messages.name}
            value={row.name}
            onChange={(event) => change(row, { name: event.target.value })}
          />
          <EnumSelect
            label={messages.role}
            value={row.role}
            options={MESSAGES.about.roles}
            onChange={(role) => change(row, { role })}
          />
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label={messages.remove(row.name)}
            onClick={() => onChange(value.filter((entry) => entry !== row))}
          >
            <Trash2Icon />
          </Button>
        </div>
      ))}
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="w-fit"
        onClick={() => onChange([...value, { name: '', role: value.length === 0 ? 'aut' : 'ctb' }])}
      >
        <PlusIcon />
        {messages.add}
      </Button>
    </fieldset>
  );
}

/** The numbers and addresses that identify the book or the copy, one row each with the kind and the value. */
export function IdentifiersField({
  value,
  onChange,
}: {
  value: readonly IdentifierSchema[];
  onChange: (value: IdentifierSchema[]) => void;
}): React.JSX.Element {
  const messages = MESSAGES.about.identifiers;
  const change = (row: IdentifierSchema, changes: Partial<IdentifierSchema>): void =>
    onChange(value.map((entry) => (entry === row ? editedRow(row, changes) : entry)));
  return (
    <fieldset className="grid gap-3">
      <legend className="mb-2 text-sm font-medium">{MESSAGES.about.fields.identifiers}</legend>
      {value.map((row) => (
        <div
          key={rowKey(row)}
          className="grid items-end gap-2 sm:grid-cols-[14rem_1fr_auto]"
          data-testid="identifier"
        >
          <EnumSelect
            label={messages.scheme}
            value={row.scheme}
            options={MESSAGES.about.schemes}
            onChange={(scheme) => change(row, { scheme })}
          />
          <TextField
            label={messages.value}
            value={row.value}
            onChange={(event) => change(row, { value: event.target.value })}
          />
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label={messages.remove(row.value)}
            onClick={() => onChange(value.filter((entry) => entry !== row))}
          >
            <Trash2Icon />
          </Button>
        </div>
      ))}
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="w-fit"
        onClick={() => onChange([...value, { scheme: 'shelfmark', value: '' }])}
      >
        <PlusIcon />
        {messages.add}
      </Button>
    </fieldset>
  );
}
