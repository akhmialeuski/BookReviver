import { useState } from 'react';
import type { EditorSession } from '@/features/editors/session';
import { RecipeSection } from '@/features/processing/RecipeSection';
import { RunControls } from '@/features/processing/RunControls';
import { SplitSection } from '@/features/processing/SplitSection';
import { StepPanel } from '@/features/processing/StepPanel';
import { ThisPageSection } from '@/features/processing/ThisPageSection';
import type { Processing } from '@/features/processing/useProcessing';
import { useStageRun } from '@/features/processing/useStageRun';
import { ProfileLibraryPanel } from '@/features/profiles/ProfileLibraryPanel';
import { StagePanel } from '@/features/workspace/StagePanel';
import type { BarStep } from '@/features/workspace/steps';
import type { StripItem } from '@/features/workspace/strip';
import type { StepWorkspace } from '@/features/workspace/useStepWorkspace';
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
  step,
}: {
  processing: Processing;
  items: readonly StripItem[];
  current: StripItem | undefined;
  selected: ReadonlySet<string>;
  /** The page editor of the stage on the open page, or null when the stage has none. */
  editor: EditorSession | null;
  /** The step that is open, whose section stands above the others, or nothing when no step is open. */
  step?: {
    workspace: StepWorkspace;
    step: BarStep;
    pageLabel: string;
    onOpen: (stepId: string | undefined) => void;
  };
}): React.JSX.Element {
  const rows = items.flatMap((item) => (item.row === undefined ? [] : [item.row]));
  const run = useStageRun(processing, items, current, selected);
  const [libraryOpen, setLibraryOpen] = useState(false);
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
      footer={<RunControls processing={processing} items={items} run={run} />}
    >
      <div className="grid gap-6">
        {step === undefined ? null : (
          <StepPanel
            processing={processing}
            workspace={step.workspace}
            step={step.step}
            pageLabel={step.pageLabel}
            pageId={current?.page.id}
            items={items}
            editor={editor}
            run={run}
            onOpen={step.onOpen}
            onClose={() => step.onOpen(undefined)}
          />
        )}
        {processing.stage === 'page-split' && current !== undefined ? (
          <SplitSection processing={processing} items={items} current={current} />
        ) : null}
        <RecipeSection
          processing={processing}
          rows={rows}
          run={run}
          pageId={current?.page.id}
          onManageProfiles={() => setLibraryOpen(true)}
          selected={selected}
        />
        {current === undefined ? null : (
          <ThisPageSection
            processing={processing}
            items={items}
            item={current}
            selected={selected}
            editor={editor}
            controls={step === undefined}
          />
        )}
      </div>
      <ProfileLibraryPanel
        open={libraryOpen}
        onOpenChange={setLibraryOpen}
        book={{ processing, items, selected }}
      />
    </StagePanel>
  );
}
