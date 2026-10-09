import { useState } from 'react';
import type { EditorSession } from '@/features/editors/session';
import { PageTimeline, type TimelineStep } from '@/features/processing/PageTimeline';
import { RecipeSection } from '@/features/processing/RecipeSection';
import { RunControls } from '@/features/processing/RunControls';
import { SplitSection } from '@/features/processing/SplitSection';
import { StepNotes, StepSettings } from '@/features/processing/StepSlots';
import { usePageValues } from '@/features/processing/usePageValues';
import type { Processing } from '@/features/processing/useProcessing';
import { useStageRun } from '@/features/processing/useStageRun';
import { useThisPage } from '@/features/processing/useThisPage';
import { ProfileLibraryPanel } from '@/features/profiles/ProfileLibraryPanel';
import { StagePanel } from '@/features/workspace/StagePanel';
import { type BarStep, hasStepBar } from '@/features/workspace/steps';
import type { StripItem } from '@/features/workspace/strip';
import type { StepWorkspace } from '@/features/workspace/useStepWorkspace';
import { MESSAGES } from '@/shared/messages';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The panel of a stage that is built from processors, which only fills the slots of the `StagePanel` every stage has:
 *
 * - `recipe`: the profile menu and the save bar, and on a stage without a step bar the picker and the list of steps;
 * - `step`: on a stage with a step bar, the title of the open step and the notes about its place in the order;
 * - `settings`: the form of the open step, the values pages have for each setting and the measurement of the book, for
 *   the step open in the bar or, on a stage without a bar, the card open in the list of the recipe;
 * - `page`: the one section "This page", which holds the choice of the Split stage for the scan, the state of the page,
 *   the page editor, the carry-over of a shape set by hand and last the facts the steps found;
 * - `history`: the changes and the results of the open step on the open page, or the results of the stage when no step is
 *   open;
 * - `footer`: the run.
 *
 * Everything inside is read from the catalogue and the recipe, so a stage with a new processor needs no change here, and
 * nothing here lays out a section: the layout does.
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
  /** The step that is open in the bar, which gives the panel its step slot; a stage with no step bar has none. */
  step?: {
    workspace: StepWorkspace;
    step: BarStep;
  };
}): React.JSX.Element {
  const { stage, catalogue } = processing;
  const rows = items.flatMap((item) => (item.row === undefined ? [] : [item.row]));
  const run = useStageRun(processing, items, current, selected);
  const values = usePageValues(processing, items, current, selected);
  const [libraryOpen, setLibraryOpen] = useState(false);
  // The open step is the one open in the bar, or on a stage without a bar the card open in the list of the recipe. The
  // settings and the history are those of this step.
  const openDraft =
    step === undefined
      ? hasStepBar(stage)
        ? undefined
        : processing.steps.find((draft) => draft.id === processing.openId)
      : processing.steps.find((draft) => draft.stepId === step.step.stepId);
  const processor = catalogue.find((entry) => entry.key === openDraft?.processorKey);
  const thisPage = useThisPage(
    processing,
    current,
    editor,
    step === undefined
      ? undefined
      : {
          stepId: step.step.stepId,
          page: step.workspace.page,
          selected,
          carried: step.workspace.carried,
          onCarried: step.workspace.setCarried,
        },
  );
  // The history of the page is the last element of the panel on every stage. With a step open it holds the changes and the
  // results of that step. With none open it holds the results of the stage. What the page stands on is told by the current
  // version of the stage, not by the row of the open step, since the recipe that ran the page may have had other steps
  // than the recipe on screen
  const historyStep: TimelineStep | null = step?.step ?? openDraft ?? null;
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
      <StagePanel
        stage={stage}
        available
        recipe={<ErrorAlert message={MESSAGES.processing.loadFailed} />}
        history={history}
      />
    );
  }
  if (!processing.ready) {
    return (
      <StagePanel
        stage={stage}
        available
        recipe={<p className="text-sm text-muted-foreground">{MESSAGES.processing.loading}</p>}
        history={history}
      />
    );
  }
  return (
    <StagePanel
      stage={stage}
      available
      recipe={
        processing.recipe === undefined ? undefined : (
          <>
            <RecipeSection
              processing={processing}
              rows={rows}
              run={run}
              onManageProfiles={() => setLibraryOpen(true)}
            />
            <ProfileLibraryPanel
              open={libraryOpen}
              onOpenChange={setLibraryOpen}
              book={{ processing }}
            />
          </>
        )
      }
      step={
        step === undefined
          ? undefined
          : {
              title: step.step.title,
              stepId: step.step.stepId,
              children: (
                <StepNotes processing={processing} draft={openDraft} enabled={step.step.enabled} />
              ),
            }
      }
      settings={
        openDraft === undefined || processor === undefined ? undefined : (
          <StepSettings
            processing={processing}
            draft={openDraft}
            processor={processor}
            values={values}
          />
        )
      }
      page={
        thisPage === undefined
          ? undefined
          : {
              ...thisPage,
              children: (
                <>
                  {stage === 'page-split' && current !== undefined ? (
                    <SplitSection processing={processing} items={items} current={current} />
                  ) : null}
                  {thisPage.children}
                </>
              ),
            }
      }
      history={history}
      footer={<RunControls processing={processing} items={items} run={run} openStep={step?.step} />}
    />
  );
}
