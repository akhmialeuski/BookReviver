import type { EditorSession } from '@/features/editors/session';
import { RecipeSection } from '@/features/processing/RecipeSection';
import { RunControls } from '@/features/processing/RunControls';
import { SplitSection } from '@/features/processing/SplitSection';
import { ThisPageSection } from '@/features/processing/ThisPageSection';
import type { Processing } from '@/features/processing/useProcessing';
import { StagePanel } from '@/features/workspace/StagePanel';
import type { StripItem } from '@/features/workspace/strip';
import { MESSAGES } from '@/shared/messages';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The panel of a stage that is built from processors: its recipe with the steps and their settings, what the stage did
 * to the open page with the results it made before, and at the foot the preview and the run.
 *
 * The frame, the name of the stage and its sentence are the `StagePanel` every stage has. Everything inside is read from
 * the catalogue and the recipe, so a stage with a new processor needs no change here.
 */

export function ProcessingPanel({
  processing,
  items,
  current,
  selected,
  editor,
}: {
  processing: Processing;
  items: readonly StripItem[];
  current: StripItem | undefined;
  selected: ReadonlySet<string>;
  /** The page editor of the stage on the open page, or null when the stage has none. */
  editor: EditorSession | null;
}): React.JSX.Element {
  const rows = items.flatMap((item) => (item.row === undefined ? [] : [item.row]));
  if (processing.failed) {
    return (
      <StagePanel stage={processing.stage} available>
        <ErrorAlert message={MESSAGES.processing.loadFailed} />
      </StagePanel>
    );
  }
  if (!processing.ready) {
    return (
      <StagePanel stage={processing.stage} available>
        <p className="text-sm text-muted-foreground">{MESSAGES.processing.loading}</p>
      </StagePanel>
    );
  }
  return (
    <StagePanel
      stage={processing.stage}
      available
      footer={
        <RunControls processing={processing} items={items} current={current} selected={selected} />
      }
    >
      <div className="grid gap-6">
        {processing.stage === 'page-split' && current !== undefined ? (
          <SplitSection processing={processing} items={items} current={current} />
        ) : null}
        <RecipeSection processing={processing} rows={rows} />
        {current === undefined ? null : (
          <ThisPageSection processing={processing} item={current} editor={editor} />
        )}
      </div>
    </StagePanel>
  );
}
