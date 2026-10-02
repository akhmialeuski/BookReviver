import { useId } from 'react';
import { cn } from '@/shared/lib/utils';

/**
 * A row of two or more choices drawn as joined buttons, of which one is chosen, over native radio buttons.
 *
 * The radios are real inputs laid over their labels with no opacity, so the arrow keys move the choice, a click lands on
 * the input itself, and a screen reader reads the group with its legend, as the browser does for any radio group.
 */

export function SegmentedRadio<T extends string>({
  legend,
  hideLegend = false,
  value,
  options,
  onChange,
}: {
  legend: string;
  /** Whether the legend is left to screen readers because the choices explain themselves. */
  hideLegend?: boolean;
  value: T;
  options: readonly { value: T; label: string }[];
  onChange: (value: T) => void;
}): React.JSX.Element {
  const name = useId();
  return (
    <fieldset className="grid min-w-0 gap-2">
      <legend className={cn('mb-2 text-sm leading-none font-medium', hideLegend ? 'sr-only' : '')}>
        {legend}
      </legend>
      <div className="grid auto-cols-fr grid-flow-col gap-1 rounded-md bg-muted p-1">
        {options.map((option) => (
          <label key={option.value} className="relative cursor-pointer">
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={option.value === value}
              onChange={() => onChange(option.value)}
              className="peer absolute inset-0 size-full cursor-pointer opacity-0"
            />
            <span className="block rounded-sm px-3 py-1 text-center text-sm text-muted-foreground peer-checked:bg-background peer-checked:font-medium peer-checked:text-foreground peer-checked:shadow-xs peer-focus-visible:ring-[3px] peer-focus-visible:ring-ring/50">
              {option.label}
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
