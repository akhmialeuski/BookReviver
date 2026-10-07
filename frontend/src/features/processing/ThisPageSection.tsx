import { TriangleAlertIcon } from 'lucide-react';
import { EditorControls } from '@/features/editors/EditorControls';
import type { EditorSession } from '@/features/editors/session';
import { ApplyTo } from '@/features/processing/ApplyTo';
import { type Fact, factsOf } from '@/features/processing/facts';
import { readChainResult } from '@/features/processing/results';
import type { Processing } from '@/features/processing/useProcessing';
import { useShownStep } from '@/features/processing/useShownStep';
import type { StripItem } from '@/features/workspace/strip';
import { MESSAGES } from '@/shared/messages';

/**
 * What the stage did to the open page.
 *
 * The facts are read from the data of the current version, so the section shows what the step found on this page and
 * not what it was asked to do. A page the step was unsure of gets an amber plate with the way out, then come the controls
 * of the page editor of the stage when it has one. The results of the stage on the page are in the history that ends the
 * panel of the stage.
 */

const labels = MESSAGES.processing;

export function ThisPageSection({
  processing,
  items,
  item,
  selected,
  editor = null,
  controls = true,
}: {
  processing: Processing;
  /** Every page of the book with where it stands in the stage, which "Apply to" counts the pages of. */
  items: readonly StripItem[];
  item: StripItem;
  /** The pages selected in the grid. */
  selected: ReadonlySet<string>;
  /** The page editor of the stage on this page, or null when the stage has none. */
  editor?: EditorSession | null;
  /** Whether the controls of the editor stand here, which they do not while the section of an open step holds them. */
  controls?: boolean;
}): React.JSX.Element {
  const { catalogue } = processing;
  const { page, row } = item;
  const version = row?.version ?? null;
  // A stage of several steps stands on the version of the last, so what the first ones found is read down the chain, up to
  // the step the reader chose to look at
  const shown = useShownStep(processing, item);
  const { chain } = shown;
  const result = readChainResult(
    shown.version === null ? chain : chain.slice(0, chain.indexOf(shown.version) + 1),
  );
  const stepsOfPage = shown.recipe?.steps ?? [];
  // The steps that are on, each by its index in the recipe, which is what the server and the panel name a step by
  const stepChoices = stepsOfPage.flatMap((entry, value) =>
    entry.enabled
      ? [
          {
            value,
            title:
              catalogue.find((candidate) => candidate.key === entry.processor_key)?.title ??
              entry.processor_key,
          },
        ]
      : [],
  );
  const review = row?.review ?? null;
  const facts: Fact[] = [];
  if (editor !== null && version !== null) {
    facts.push({
      label: labels.thisPage.how,
      value: chain.some((step) => step.edit_hash !== '')
        ? labels.thisPage.manual
        : labels.thisPage.automatic,
    });
  }
  if (result !== null) {
    facts.push(...factsOf(result, review !== null));
  }

  return (
    <section
      className="grid gap-3"
      aria-label={labels.thisPage.title(page.label)}
      data-testid="this-page"
    >
      <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
        {labels.thisPage.title(page.label)}
      </h3>
      {row?.status === 'failed' ? (
        <p className="text-sm text-status-failed" data-testid="this-page-failed">
          {labels.thisPage.failed(version?.error ?? '')}
        </p>
      ) : version === null ? (
        <p className="text-sm text-muted-foreground">{labels.thisPage.notProcessed}</p>
      ) : (
        <>
          {row?.status === 'stale' ? (
            <p className="text-sm text-status-attention">{labels.thisPage.outOfDate}</p>
          ) : null}
          <dl
            className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm"
            data-testid="this-page-facts"
          >
            {facts.map((fact) => (
              <div key={fact.label} className="col-span-2 flex justify-between gap-4">
                <dt className="text-muted-foreground">{fact.label}</dt>
                <dd className="font-medium">{fact.value}</dd>
              </div>
            ))}
          </dl>
        </>
      )}
      {row?.through_step === null || row?.through_step === undefined ? null : (
        <p className="text-sm text-status-attention" data-testid="this-page-stopped">
          {labels.thisPage.stoppedAt(row.through_step + 1, stepsOfPage.length)}
        </p>
      )}
      {stepsOfPage.length < 2 || chain.length === 0 ? null : (
        <label className="grid gap-1 text-sm">
          <span className="text-muted-foreground">{labels.thisPage.result.label}</span>
          <select
            className="h-9 w-full min-w-0 rounded-md border border-input bg-background px-3 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
            data-testid="this-page-step"
            value={shown.index === null ? '' : String(shown.index)}
            onChange={(event) =>
              processing.showStep(event.target.value === '' ? null : Number(event.target.value))
            }
          >
            <option value="">{labels.thisPage.result.last}</option>
            {stepChoices.map(({ value, title }) => (
              <option key={value} value={value}>
                {labels.thisPage.result.option(value + 1, title)}
              </option>
            ))}
          </select>
        </label>
      )}
      {shown.reached || shown.index === null ? null : (
        <p className="text-xs text-muted-foreground" data-testid="this-page-not-reached">
          {labels.thisPage.result.notReached(shown.index + 1)}
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
      {editor === null || !controls ? null : <EditorControls session={editor} />}
      <ApplyTo processing={processing} items={items} item={item} selected={selected} />
    </section>
  );
}
