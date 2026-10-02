import { RotateCcwIcon } from 'lucide-react';
import { useRef, useState } from 'react';
import { parseAngle } from '@/features/editors/rotation';
import { MESSAGES } from '@/shared/messages';
import { Input } from '@/shared/ui/input';

/**
 * The field that holds the angle of the rotation editor in the panel, next to the handle on the page.
 *
 * What the reader types is kept as typed until they leave the field or press Enter, and then it is read, rounded to a
 * tenth of a degree and saved. Text that is not a number is dropped, and the field shows the angle it had.
 */

const labels = MESSAGES.editors.rotation;

export function AngleField({
  degrees,
  disabled,
  onCommit,
}: {
  degrees: number;
  disabled: boolean;
  onCommit: (degrees: number) => void;
}): React.JSX.Element {
  const [typed, setTyped] = useState<string | null>(null);
  // Escape leaves the field, and the blur that follows must not save what was typed
  const cancelled = useRef(false);

  const finish = (): void => {
    if (typed === null) {
      return;
    }
    if (cancelled.current) {
      cancelled.current = false;
      setTyped(null);
      return;
    }
    const angle = parseAngle(typed);
    setTyped(null);
    if (angle !== null && angle !== degrees) {
      onCommit(angle);
    }
  };

  return (
    <div className="relative w-28">
      <RotateCcwIcon
        className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
        aria-hidden="true"
      />
      <Input
        type="text"
        inputMode="decimal"
        aria-label={labels.angle}
        data-testid="angle-field"
        className="px-9"
        disabled={disabled}
        value={typed ?? degrees.toFixed(1)}
        onFocus={(event) => event.currentTarget.select()}
        onChange={(event) => setTyped(event.target.value)}
        onBlur={finish}
        onKeyDown={(event) => {
          if (event.key === 'Enter') {
            event.currentTarget.blur();
          } else if (event.key === 'Escape') {
            cancelled.current = true;
            event.currentTarget.blur();
          }
        }}
      />
      <span
        className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-sm text-muted-foreground"
        aria-hidden="true"
      >
        °
      </span>
    </div>
  );
}
