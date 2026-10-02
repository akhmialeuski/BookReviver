import { AngleField } from '@/features/editors/AngleField';
import type { RotationShape } from '@/features/editors/shapes';
import type { PanelProps } from '@/features/editors/types';

/** The part of the rotation editor in the panel: the field that holds the angle. */
export function RotationPanel({ shape, disabled, onCommit }: PanelProps<RotationShape>) {
  return (
    <AngleField
      degrees={shape.degrees}
      disabled={disabled}
      onCommit={(degrees) => onCommit({ degrees })}
    />
  );
}
