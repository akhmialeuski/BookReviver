import { AngleField } from '@/features/editors/AngleField';
import {
  ANGLE_KEY_STEP,
  ANGLE_LIMIT,
  formatAngle,
  limitFineAngle,
} from '@/features/editors/rotation';
import type { RotationShape } from '@/features/editors/shapes';
import type { PanelProps } from '@/features/editors/types';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { Slider } from '@/shared/ui/slider';

/**
 * The part of the rotation editor in the panel: a slider over the range of angles the step looks in, the field that holds
 * the angle, and the button that lays the page level.
 *
 * The slider ends at the largest slant the settings of the step allow, which is the range the automatic search covers, and
 * not at the largest angle the editor accepts. An angle set outside it, by the field, shows at the end of the slider.
 */

const labels = MESSAGES.editors.rotation;

/** Read the largest slant of the settings of a step, or the limit of the editor when the step has none. */
function limitOf(params: Readonly<Record<string, unknown>>): number {
  const value = params.max_angle;
  return typeof value === 'number' && Number.isFinite(value) && value > 0
    ? Math.min(value, ANGLE_LIMIT)
    : ANGLE_LIMIT;
}

export function RotationPanel({
  shape,
  params,
  disabled,
  onChange,
  onCommit,
}: PanelProps<RotationShape>): React.JSX.Element {
  const limit = limitOf(params);
  const range = labels.range(limit);
  return (
    <div className="grid basis-full gap-2" data-testid="rotation-panel">
      <div className="grid gap-1 text-sm">
        <div className="flex items-center gap-3">
          <span className="w-10 shrink-0 text-muted-foreground">{range.least}</span>
          <Slider
            min={-limit}
            max={limit}
            step={ANGLE_KEY_STEP}
            value={[Math.min(Math.max(shape.degrees, -limit), limit)]}
            disabled={disabled}
            thumbLabel={labels.slider}
            data-testid="angle-slider"
            // The page turns as the slider moves, and the angle is saved once the reader lets go of it
            onValueChange={([next]) =>
              next !== undefined && onChange({ degrees: limitFineAngle(next) })
            }
            onValueCommit={([next]) =>
              next !== undefined && onCommit({ degrees: limitFineAngle(next) })
            }
          />
          <span className="w-10 shrink-0 text-right text-muted-foreground">{range.most}</span>
        </div>
        <p className="text-xs text-muted-foreground">{labels.hint}</p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <AngleField
          degrees={shape.degrees}
          disabled={disabled}
          onCommit={(degrees) => onCommit({ degrees })}
        />
        <Button
          variant="outline"
          size="sm"
          disabled={disabled || shape.degrees === 0}
          title={labels.zeroTitle}
          data-testid="angle-zero"
          onClick={() => onCommit({ degrees: 0 })}
        >
          {formatAngle(0)}°
        </Button>
      </div>
    </div>
  );
}
