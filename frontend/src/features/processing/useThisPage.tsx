import { TriangleAlertIcon } from 'lucide-react';
import type { CarryOverSchema, StepPageSchema } from '@/api';
import { EditorControls } from '@/features/editors/EditorControls';
import type { EditorSession } from '@/features/editors/session';
import { type Fact, factsOf } from '@/features/processing/facts';
import { readChainResult } from '@/features/processing/results';
import { StepCarry } from '@/features/processing/StepSlots';
import { usePageChain } from '@/features/processing/usePageChain';
import type { Processing } from '@/features/processing/useProcessing';
import { FactList } from '@/features/workspace/FactList';
import type { PageSlot } from '@/features/workspace/StagePanel';
import { barStepsOf } from '@/features/workspace/steps';
import type { StripItem } from '@/features/workspace/strip';
import { MESSAGES } from '@/shared/messages';

/**
 * What the stage did to the open page, as the content of the `page` slot of the panel: a note on the state of the page,
 * the amber plate for a page the step was unsure of with the way out, the controls of the page editor, and for the open
 * step the carry-over, with the facts the steps found last.
 *
 * The facts are read from the data of the current version, so the section shows what the steps found on this page and not
 * what they were asked to do. There is one place for the controls of the editor, whether a step is open or not. The
 * results of the stage on the page are in the history that ends the panel of the stage.
 */

const labels = MESSAGES.processing;
const stepLabels = MESSAGES.workspace.stepPanel;

/** The open step of a stage with a bar, and what it did on the open page. */
export interface OpenStep {
  /** The identifier of the open step. */
  stepId: string;
  /** What the step read and made on the open page, or null while it is read. */
  page: StepPageSchema | null;
  /** The pages selected in the grid, which a shape set by hand can be carried over to. */
  selected: ReadonlySet<string>;
  /** What the last carry-over of the step did, kept above the page so that it outlives it, or null when there is none. */
  carried: CarryOverSchema | null;
  /** Called with what a carry-over did, and with null once it was taken back. */
  onCarried: (result: CarryOverSchema | null) => void;
}

/**
 * Build the page slot of the panel.
 *
 * @param processing The state of the stage panel.
 * @param item The open page with its row in the stage, or undefined when the book has no page, which leaves the slot out.
 * @param editor The page editor of the stage on this page, or null when the stage has none.
 * @param step The open step of a stage with a bar, or undefined for a stage without one.
 */
export function useThisPage(
  processing: Processing,
  item: StripItem | undefined,
  editor: EditorSession | null,
  step?: OpenStep,
): PageSlot | undefined {
  // A stage of several steps stands on the version of the last, so what the first ones found is read down the chain
  const { recipe, chain } = usePageChain(processing, item);
  if (item === undefined) {
    return undefined;
  }
  const { page, row } = item;
  const version = row?.version ?? null;
  const result = readChainResult(chain);
  const review = row?.review ?? null;
  const facts: Fact[] = [];
  if (editor !== null && version !== null) {
    facts.push({
      label: labels.thisPage.how,
      value: chain.some((entry) => entry.edit_hash !== '')
        ? labels.thisPage.manual
        : labels.thisPage.automatic,
    });
  }
  if (result !== null) {
    facts.push(...factsOf(result, review !== null));
  }
  const stepPage = step?.page ?? null;
  const notReached =
    stepPage !== null && stepPage.version === null && stepPage.input_version === null;

  return {
    title: labels.thisPage.title(page.label),
    children: (
      <>
        {row?.status === 'failed' ? (
          <p className="text-sm text-status-failed" data-testid="this-page-failed">
            {labels.thisPage.failed(version?.error ?? '')}
          </p>
        ) : version === null ? (
          <p className="text-sm text-muted-foreground">{labels.thisPage.notProcessed}</p>
        ) : row?.status === 'stale' ? (
          <p className="text-sm text-status-attention">{labels.thisPage.outOfDate}</p>
        ) : null}
        {row?.through_step === null || row?.through_step === undefined ? null : (
          <p className="text-sm text-status-attention" data-testid="this-page-stopped">
            {labels.thisPage.stoppedAt(
              barStepsOf(recipe, processing.catalogue)[row.through_step]?.title ?? '',
            )}
          </p>
        )}
        {review === null ? null : (
          <div
            className="grid gap-2 rounded-lg border border-status-attention/60 bg-status-attention/10 p-3 text-sm"
            data-testid="this-page-review"
          >
            <p className="flex items-start gap-2 font-medium">
              <TriangleAlertIcon
                className="mt-0.5 size-4 shrink-0 text-status-attention"
                aria-hidden="true"
              />
              {labels.thisPage.reviewTitle[review]}
            </p>
            <p className="text-muted-foreground">{labels.thisPage.reviewHint}</p>
          </div>
        )}
        {notReached ? (
          <p className="text-xs text-muted-foreground" data-testid="step-panel-not-reached">
            {stepLabels.notReached}
          </p>
        ) : null}
        {editor === null ? null : <EditorControls session={editor} />}
        {step !== undefined && stepPage?.state === 'by-hand' ? (
          <StepCarry
            processing={processing}
            pageId={page.id}
            stepId={step.stepId}
            selected={step.selected}
            result={step.carried}
            onResult={step.onCarried}
          />
        ) : null}
      </>
    ),
    facts:
      row?.status === 'failed' || version === null || facts.length === 0 ? undefined : (
        <FactList facts={facts} data-testid="this-page-facts" />
      ),
  };
}
