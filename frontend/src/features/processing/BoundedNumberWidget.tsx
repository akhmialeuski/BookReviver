import type { WidgetProps } from '@rjsf/utils';
import { useState } from 'react';
import { sliderSpecOf, snapToSlider } from '@/features/processing/schema';
import { Input } from '@/shared/ui/input';
import { Slider } from '@/shared/ui/slider';

/**
 * The widget of a number with both bounds: a slider for a rough drag and an input for an exact value.
 *
 * The input takes any number the reader types, in bounds or not, and a value out of its bounds is flagged by the
 * form's own validation and keeps the recipe from being saved, so a typed 99 is never quietly turned into 45. The
 * slider holds the nearest value it can show.
 */

export function BoundedNumberWidget({
  id,
  value,
  label,
  schema,
  disabled,
  readonly,
  rawErrors,
  onChange,
}: WidgetProps): React.JSX.Element {
  const spec = sliderSpecOf(schema);
  const number = typeof value === 'number' ? value : null;
  const [typed, setTyped] = useState<{ text: string; for: number | null }>({
    text: number === null ? '' : String(number),
    for: number,
  });
  // The slider and the form can change the value under the input, which then shows it
  const text = typed.for === number ? typed.text : number === null ? '' : String(number);

  const type = (next: string): void => {
    const parsed = next.trim() === '' ? null : Number(next);
    if (parsed !== null && Number.isNaN(parsed)) {
      return;
    }
    setTyped({ text: next, for: parsed });
    onChange(parsed ?? undefined);
  };

  return (
    <div className="flex items-center gap-3 p-0.5">
      {spec === null ? null : (
        <Slider
          min={spec.min}
          max={spec.max}
          step={spec.step}
          value={[snapToSlider(number ?? spec.min, spec)]}
          disabled={disabled || readonly}
          thumbLabel={label}
          onValueChange={([next]) => next !== undefined && onChange(next)}
        />
      )}
      <Input
        id={id}
        type="number"
        inputMode="decimal"
        className="w-24 shrink-0"
        min={spec?.min}
        max={spec?.max}
        step={spec?.step}
        value={text}
        disabled={disabled}
        readOnly={readonly}
        aria-invalid={(rawErrors?.length ?? 0) > 0}
        onChange={(event) => type(event.target.value)}
      />
    </div>
  );
}
