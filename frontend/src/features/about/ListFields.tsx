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

/** A field of rows that can be edited, removed and added to, which is what contributors and identifiers both are. */
function RowsField<T extends object>({
  legend,
  value,
  onChange,
  columns,
  testId,
  addLabel,
  removeLabel,
  newRow,
  cells,
}: {
  legend: string;
  value: readonly T[];
  onChange: (value: T[]) => void;
  /** The classes that lay the cells of a row and its remove button in a grid. */
  columns: string;
  testId: string;
  addLabel: string;
  /** Give the name of the button that removes a row, which says whose row it is. */
  removeLabel: (row: T) => string;
  /** Give the row that "Add" appends, given the rows there are. */
  newRow: (rows: readonly T[]) => T;
  /** Draw the fields of a row, with the function that changes some of its values. */
  cells: (row: T, change: (changes: Partial<T>) => void) => React.ReactNode;
}): React.JSX.Element {
  const change = (row: T, changes: Partial<T>): void =>
    onChange(value.map((entry) => (entry === row ? editedRow(row, changes) : entry)));
  return (
    <fieldset className="grid gap-3">
      <legend className="mb-2 text-sm font-medium">{legend}</legend>
      {value.map((row) => (
        <div key={rowKey(row)} className={`grid items-end gap-2 ${columns}`} data-testid={testId}>
          {cells(row, (changes) => change(row, changes))}
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label={removeLabel(row)}
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
        onClick={() => onChange([...value, newRow(value)])}
      >
        <PlusIcon />
        {addLabel}
      </Button>
    </fieldset>
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
  return (
    <RowsField<ContributorSchema>
      legend={MESSAGES.about.fields.contributors}
      value={value}
      onChange={onChange}
      columns="sm:grid-cols-[1fr_14rem_auto]"
      testId="contributor"
      addLabel={messages.add}
      removeLabel={(row) => messages.remove(row.name)}
      newRow={(rows) => ({ name: '', role: rows.length === 0 ? 'aut' : 'ctb' })}
      cells={(row, change) => (
        <>
          <TextField
            label={messages.name}
            value={row.name}
            onChange={(event) => change({ name: event.target.value })}
          />
          <EnumSelect
            label={messages.role}
            value={row.role}
            options={MESSAGES.about.roles}
            onChange={(role) => change({ role })}
          />
        </>
      )}
    />
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
  return (
    <RowsField<IdentifierSchema>
      legend={MESSAGES.about.fields.identifiers}
      value={value}
      onChange={onChange}
      columns="sm:grid-cols-[14rem_1fr_auto]"
      testId="identifier"
      addLabel={messages.add}
      removeLabel={(row) => messages.remove(row.value)}
      newRow={() => ({ scheme: 'shelfmark', value: '' })}
      cells={(row, change) => (
        <>
          <EnumSelect
            label={messages.scheme}
            value={row.scheme}
            options={MESSAGES.about.schemes}
            onChange={(scheme) => change({ scheme })}
          />
          <TextField
            label={messages.value}
            value={row.value}
            onChange={(event) => change({ value: event.target.value })}
          />
        </>
      )}
    />
  );
}
