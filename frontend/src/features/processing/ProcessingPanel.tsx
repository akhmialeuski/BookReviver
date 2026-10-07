import { useState } from 'react';
import type { EditorSession } from '@/features/editors/session';
import { ContentTypeSection } from '@/features/processing/ContentTypeSection';
import { PageTimeline, type TimelineStep } from '@/features/processing/PageTimeline';
import { RecipeSection } from '@/features/processing/RecipeSection';
import { RunControls } from '@/features/processing/RunControls';
import { SplitSection } from '@/features/processing/SplitSection';
import { StepPanel } from '@/features/processing/StepPanel';
import { ThisPageSection } from '@/features/processing/ThisPageSection';
import type { Processing } from '@/features/processing/useProcessing';
import { useStageRun } from '@/features/processing/useStageRun';
import { ProfileLibraryPanel } from '@/features/profiles/ProfileLibraryPanel';
import { StagePanel } from '@/features/workspace/StagePanel';
import { type BarStep, hasStepBar } from '@/features/workspace/steps';
import type { StripItem } from '@/features/workspace/strip';
import type { StepWorkspace } from '@/features/workspace/useStepWorkspace';
import { MESSAGES } from '@/shared/messages';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The panel of a stage that is built from processors: its recipe with the steps and their settings, what the pages show,
 * what the stage did to the open page, the history of the page as the last element, and at the foot the preview and the
 * run.
 *
 * The frame, the name of the stage and its sentence are the `StagePanel` every stage has, and the history is the content
 * this panel gives it: the changes and the results of the open step on the open page, or the results of the stage when no
 * step is open. Everything inside is read from the catalogue and the recipe, so a stage with a new processor needs no
 * change here.
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
  /** The step that is open, whose section stands above the others; a stage with no step bar has none. */
  step?: {
    workspace: StepWorkspace;
    step: BarStep;
    pageLabel: string;
    onOpen: (stepId: string) => void;
  };
}): React.JSX.Element {
  const rows = items.flatMap((item) => (item.row === undefined ? [] : [item.row]));
  const run = useStageRun(processing, items, current, selected);
  const [libraryOpen, setLibraryOpen] = useState(false);
  // The history of the page is the last element of the panel on every stage. With a step open it holds the changes and the
  // results of that step: the step open in the bar, or on a stage without a bar the step open in the list of the recipe.
  // With none open it holds the results of the stage. What the page stands on is told by the current version of the stage,
  // not by the row of the open step, since a variant of the recipe may have run the page
  const historyStep: TimelineStep | null =
    step?.step ??
    (hasStepBar(processing.stage)
      ? null
      : (processing.steps.find((draft) => draft.id === processing.openId) ?? null));
  const history = (
    <PageTimeline
      key={`${current?.page.id}|${historyStep?.stepId}|${historyStep?.processorKey}`}
      processing={processing}
      pageId={current?.page.id}
      step={historyStep}
      headId={current?.row?.version?.id}
    />
  );
  if (processing.failed) {
    return (
      <StagePanel stage={processing.stage} available history={history}>
        <ErrorAlert message={MESSAGES.processing.loadFailed} />
      </StagePanel>
    );
  }
  if (!processing.ready) {
    return (
      <StagePanel stage={processing.stage} available history={history}>
        <p className="text-sm text-muted-foreground">{MESSAGES.processing.loading}</p>
      </StagePanel>
    );
  }
  return (
    <StagePanel
      stage={processing.stage}
      available
      history={history}
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
            selected={selected}
            editor={editor}
            run={run}
            onOpen={step.onOpen}
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
        {processing.stage === 'page-split' ? null : (
          <ContentTypeSection
            projectId={processing.projectId}
            items={items}
            currentId={current?.page.id}
            selected={selected}
          />
        )}
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
