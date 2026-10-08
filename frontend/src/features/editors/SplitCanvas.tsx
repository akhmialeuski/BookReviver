import { LineCanvas } from '@/features/editors/LineCanvas';
import { cutLine } from '@/features/editors/line';
import type { LineShape, SplitShape } from '@/features/editors/shapes';
import type { CanvasProps } from '@/features/editors/types';
import { readResult } from '@/features/processing/results';
import { PAGES_OF, SplitChoice } from '@/features/processing/split';

/**
 * The cut of a scan in the choice of pages: the split line, drawn and moved as the line editor does.
 *
 * A choice that has no line, such as "Two pages" pressed in the panel, still shows the cut the step found, and moving the
 * line makes the choice two pages with that line.
 */

const NO_SIZE = { width: 0, height: 0 };

function chosen(line: LineShape): SplitShape {
  return { pages: PAGES_OF[SplitChoice.Two], line };
}

export function SplitCanvas({
  scene,
  shape,
  size,
  context,
  figure,
  onChange,
  onCommit,
  onCommitLater,
}: CanvasProps<SplitShape>): React.JSX.Element {
  const version = context.current.row?.version;
  const found = version === undefined || version === null ? null : readResult(version);
  return (
    <LineCanvas
      scene={scene}
      shape={shape.line ?? cutLine(found, size ?? NO_SIZE)}
      size={size}
      context={context}
      figure={figure}
      onChange={(line) => onChange(chosen(line))}
      onCommit={(line) => onCommit(chosen(line))}
      onCommitLater={(line) => onCommitLater(chosen(line))}
    />
  );
}
