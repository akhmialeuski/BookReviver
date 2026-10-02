import { SelectField } from '@/shared/ui/select-field';

/**
 * A drop-down list over a closed set of values, such as the orthography or the role of a contributor.
 *
 * The options are the keys of a record of labels, so the list and the labels cannot drift apart, and a value the
 * browser reports is checked against the keys instead of being trusted to be one of them.
 */

function isOption<T extends string>(options: Record<T, string>, value: string): value is T {
  return Object.hasOwn(options, value);
}

export function EnumSelect<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  /** The label of every value of the set. */
  options: Record<T, string>;
  onChange: (value: T) => void;
}): React.JSX.Element {
  return (
    <SelectField
      label={label}
      value={value}
      onChange={(event) => {
        const next = event.target.value;
        if (isOption(options, next)) {
          onChange(next);
        }
      }}
    >
      {Object.entries<string>(options).map(([option, text]) => (
        <option key={option} value={option}>
          {text}
        </option>
      ))}
    </SelectField>
  );
}
