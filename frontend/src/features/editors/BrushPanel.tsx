import { EraserIcon } from 'lucide-react';
import {
  BRUSH_MAX_PERCENT,
  BRUSH_MIN_PERCENT,
  BRUSH_STEP_PERCENT,
  useBrushPercent,
} from '@/features/editors/brush';
import type { BrushShape } from '@/features/editors/shapes';
import type { PanelProps } from '@/features/editors/types';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { Slider } from '@/shared/ui/slider';

/**
 * The part of the brush editor in the panel: the sentence that says how the brush works, the size of the brush, and the
 * button that takes every stroke back.
 */

const labels = MESSAGES.editors.brush;

export function BrushPanel({
  shape,
  disabled,
  onCommit,
}: PanelProps<BrushShape>): React.JSX.Element {
  const [percent, setPercent] = useBrushPercent();
  return (
    <div className="grid basis-full gap-2" data-testid="brush-panel">
      <p className="rounded-lg border bg-muted/40 p-3 text-sm" data-testid="brush-hint">
        {labels.hint}
      </p>
      <div className="grid gap-1 text-sm">
        <span className="font-medium">{labels.size}</span>
        <div className="flex items-center gap-3">
          <Slider
            min={BRUSH_MIN_PERCENT}
            max={BRUSH_MAX_PERCENT}
            step={BRUSH_STEP_PERCENT}
            value={[percent]}
            disabled={disabled}
            thumbLabel={labels.size}
            onValueChange={([next]) => next !== undefined && setPercent(next)}
          />
          <span className="w-44 shrink-0 text-muted-foreground" data-testid="brush-size">
            {labels.sizeValue(percent)}
          </span>
        </div>
      </div>
      <Button
        variant="outline"
        size="sm"
        className="justify-self-start"
        disabled={disabled || shape.strokes.length === 0}
        data-testid="brush-clear"
        onClick={() => onCommit({ strokes: [] })}
      >
        <EraserIcon />
        {labels.clear}
      </Button>
    </div>
  );
}
