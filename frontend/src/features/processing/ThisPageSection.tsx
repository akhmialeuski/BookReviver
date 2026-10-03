import { TriangleAlertIcon } from 'lucide-react';
import { stepChain } from '@/features/editors/chain';
import { EditorControls } from '@/features/editors/EditorControls';
import type { EditorSession } from '@/features/editors/session';
import { ApplyTo } from '@/features/processing/ApplyTo';
import { useChooseVersion, useVersions } from '@/features/processing/queries';
import { describeParams, historyOf, readChainResult } from '@/features/processing/results';
import type { Processing } from '@/features/processing/useProcessing';
import type { StripItem } from '@/features/workspace/strip';
import { describeError } from '@/shared/http/problem';
import { formatDateTime } from '@/shared/lib/format';
import { MESSAGES } from '@/shared/messages';
import { Badge } from '@/shared/ui/badge';
import { Button } from '@/shared/ui/button';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * What the stage did to the open page, and the results it made on it before.
 *
 * The facts are read from the data of the current version, so the section shows what the step found on this page and
 * not what it was asked to do. A page the step was unsure of gets an amber plate with the way out, then come the controls
 * of the page editor of the stage when it has one, and under them stands the history of the results, from which an earlier
 * one is made the current one.
 */

const labels = MESSAGES.processing;

export function ThisPageSection({
  processing,
  items,
  item,
  selected,
  editor = null,
}: {
  processing: Processing;
  /** Every page of the book with where it stands in the stage, which "Apply to" counts the pages of. */
  items: readonly StripItem[];
  item: StripItem;
  /** The pages selected in the grid. */
  selected: ReadonlySet<string>;
  /** The page editor of the stage on this page, or null when the stage has none. */
  editor?: EditorSession | null;
}): React.JSX.Element {
  const { projectId, stage, catalogue } = processing;
  const { page, row } = item;
  const version = row?.version ?? null;
  const versions = useVersions(projectId, page.id, stage);
  const choose = useChooseVersion(projectId, stage);
  const entries = historyOf(versions.data ?? [], version?.id);
  // A stage of several steps stands on the version of the last, so what the first ones found is read down the chain
  const chain = stepChain(versions.data ?? [], version);
  const result = readChainResult(chain);
  const review = row?.review ?? null;
  const facts: { label: string; value: string }[] = [];
  if (editor !== null && version !== null) {
    facts.push({
      label: labels.thisPage.how,
      value: chain.some((step) => step.edit_hash !== '')
        ? labels.thisPage.manual
        : labels.thisPage.automatic,
    });
  }
  if (result !== null) {
    if (result.skipped) {
      facts.push({ label: labels.thisPage.method, value: labels.thisPage.left });
    } else if (result.angle !== null) {
      facts.push({ label: labels.thisPage.angle, value: labels.thisPage.degrees(result.angle) });
    }
    if (result.pages !== null) {
      facts.push({ label: labels.thisPage.pages, value: labels.thisPage.pagesValue(result.pages) });
    }
    if (result.cutX !== null) {
      facts.push({ label: labels.thisPage.cut, value: labels.thisPage.pixels(result.cutX) });
    }
    if (result.slantDeg !== null) {
      facts.push({ label: labels.thisPage.slant, value: labels.thisPage.degrees(result.slantDeg) });
    }
    if (result.overlapPx !== null) {
      facts.push({
        label: labels.thisPage.overlap,
        value: labels.thisPage.pixels(result.overlapPx),
      });
    }
    if (result.bend !== null) {
      facts.push({ label: labels.thisPage.bend, value: labels.thisPage.bendValue(result.bend) });
    }
    if (result.lines !== null) {
      facts.push({ label: labels.thisPage.lines, value: String(result.lines) });
    }
    if (result.confidence !== null) {
      facts.push({
        label: labels.thisPage.confidence,
        value:
          review === null
            ? labels.thisPage.sure(result.confidence)
            : labels.thisPage.unsure(result.confidence),
      });
    }
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
      {editor === null ? null : <EditorControls session={editor} />}
      <ApplyTo processing={processing} items={items} item={item} selected={selected} />

      <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
        {labels.history.title}
      </h3>
      {entries.length === 0 ? (
        <p className="text-sm text-muted-foreground">{labels.history.empty}</p>
      ) : (
        <ol className="grid gap-2" data-testid="history">
          {entries.map(({ version: entry, current }) => {
            const processor = catalogue.find((candidate) => candidate.key === entry.processor.key);
            const parts = describeParams(entry.params, processor?.parameters ?? {});
            return (
              <li
                key={entry.id}
                className="flex items-start gap-2 rounded-lg border px-3 py-2 text-sm"
                data-testid="history-entry"
                data-current={current}
              >
                <div className="grid min-w-0 flex-1 gap-0.5">
                  <span className="text-muted-foreground">
                    {labels.history.made(formatDateTime(entry.created_at))}
                  </span>
                  <span className="break-words">
                    {parts.map((part) => `${part.label} ${part.value}`).join(' · ')}
                  </span>
                </div>
                {current ? (
                  <Badge variant="secondary">{labels.history.current}</Badge>
                ) : (
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={choose.isPending}
                    data-testid="history-use"
                    onClick={() =>
                      choose.mutate({
                        path: { project_id: projectId, page_id: page.id, stage },
                        body: { version_id: entry.id },
                      })
                    }
                  >
                    {choose.isPending && choose.variables?.body.version_id === entry.id
                      ? labels.history.using
                      : labels.history.use}
                  </Button>
                )}
              </li>
            );
          })}
        </ol>
      )}
      {choose.error === null ? null : <ErrorAlert message={describeError(choose.error)} />}
    </section>
  );
}
