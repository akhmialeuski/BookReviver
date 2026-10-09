import type { UseQueryResult } from '@tanstack/react-query';
import { CheckIcon, TriangleAlertIcon } from 'lucide-react';
import { useMemo } from 'react';
import type { PageKind, PageSchema } from '@/api';
import {
  clampStart,
  endOfRunBefore,
  type NumberingDraft,
  numberingBody,
  pagesInRange,
} from '@/features/order/numbering';
import { useNumberPages } from '@/features/pages/actions';
import { describePageError } from '@/features/pages/errors';
import {
  findGaps,
  formatNumber,
  hiddenGaps,
  type LabelGap,
  withLabels,
} from '@/features/pages/gaps';
import { LABEL_STYLES, PAGE_KINDS } from '@/features/pages/kinds';
import { SegmentedRadio } from '@/features/pages/SegmentedRadio';
import { StagePanel } from '@/features/workspace/StagePanel';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { CheckboxField } from '@/shared/ui/checkbox-field';
import { ErrorAlert } from '@/shared/ui/error-alert';
import { SelectField } from '@/shared/ui/select-field';
import { TextField } from '@/shared/ui/text-field';

/**
 * The panel of the Order stage while pages are being numbered, in the slots of the `StagePanel`: the title of the
 * numbering and what it does stand in the step slot, the range, the first number, the style, the brackets and the kinds of
 * page that take no number in the settings frame, with the count of pages that get a new number and the button that saves
 * in the footer.
 *
 * Every change asks the server for the labels the numbering would write, and the grid shows them in blue over the old
 * ones, so nothing is saved until "Apply numbers" and what is saved is what was shown. A numbering that would close
 * a jump in the printed numbers says so, with the two ways out: add the missing pages first, or number in two runs.
 */

export function NumberingPanel({
  projectId,
  pages,
  draft,
  preview,
  gaps,
  adding,
  onDraft,
  onClose,
  onAddMissing,
}: {
  projectId: string;
  pages: readonly PageSchema[];
  draft: NumberingDraft;
  /** The labels the numbering would write, by page id. */
  preview: UseQueryResult<Map<string, string>>;
  /** The gaps in the numbers the pages have now. */
  gaps: readonly LabelGap[];
  /** Whether placeholders for a gap are being added. */
  adding: boolean;
  onDraft: (draft: NumberingDraft) => void;
  onClose: () => void;
  /** Add a placeholder for each number the given gaps lack. */
  onAddMissing: (gaps: readonly LabelGap[]) => void;
}): React.JSX.Element {
  const text = MESSAGES.order.numbering;
  const apply = useNumberPages(projectId);
  const range = pagesInRange(pages, draft);
  const runsForward = range !== null;
  const labels = preview.data;

  // The jumps the new numbers would close: the gaps there are now that are not gaps among the new labels
  const hidden = useMemo(
    () =>
      labels === undefined || !runsForward
        ? []
        : hiddenGaps(gaps, findGaps(withLabels(pages, labels))),
    [gaps, labels, pages, runsForward],
  );
  const [firstHidden] = hidden;

  const numbered = labels?.size ?? 0;
  const skipped = (range?.length ?? 0) - numbered;
  const options = pages.map((page) => (
    <option key={page.id} value={page.id}>
      {text.page(page.position + 1, page.label, MESSAGES.pages.kinds[page.kind])}
    </option>
  ));
  const noNumbers = draft.style === 'none';

  const toggleKind = (kind: PageKind, skip: boolean): void =>
    onDraft({
      ...draft,
      skipKinds: skip
        ? [...draft.skipKinds, kind]
        : draft.skipKinds.filter((candidate) => candidate !== kind),
    });

  const footer = (
    <div className="grid gap-3">
      <p className="text-sm text-muted-foreground" data-testid="numbering-counts">
        {labels === undefined ? text.loading : text.counts(numbered, skipped)}
      </p>
      {apply.isError ? <ErrorAlert message={describePageError(apply.error)} /> : null}
      <div className="grid grid-cols-2 gap-2">
        <Button variant="outline" onClick={onClose}>
          {MESSAGES.common.cancel}
        </Button>
        <Button
          disabled={
            apply.isPending ||
            preview.isFetching ||
            preview.isError ||
            range === null ||
            labels === undefined
          }
          onClick={() =>
            apply.mutate(
              { path: { project_id: projectId }, body: numberingBody(draft) },
              { onSuccess: onClose },
            )
          }
        >
          <CheckIcon />
          {apply.isPending ? text.applying : text.apply}
        </Button>
      </div>
    </div>
  );

  return (
    <StagePanel
      stage="page-order"
      available
      step={{
        title: text.title,
        children: <p className="text-sm text-muted-foreground">{text.description}</p>,
      }}
      settings={
        <div className="grid gap-4" data-testid="numbering-panel">
          <div className="grid grid-cols-2 gap-3">
            <SelectField
              label={text.first}
              value={draft.firstId}
              onChange={(event) => onDraft({ ...draft, firstId: event.target.value })}
            >
              {options}
            </SelectField>
            <SelectField
              label={text.last}
              value={draft.lastId}
              onChange={(event) => onDraft({ ...draft, lastId: event.target.value })}
            >
              {options}
            </SelectField>
          </div>
          {range === null ? <ErrorAlert message={text.backwards} /> : null}

          <TextField
            label={text.start}
            type="number"
            min={1}
            value={draft.start}
            disabled={noNumbers}
            onChange={(event) =>
              onDraft({ ...draft, start: clampStart(draft.style, event.target.valueAsNumber) })
            }
          />

          <SegmentedRadio
            legend={text.style}
            value={draft.style}
            options={LABEL_STYLES.map((style) => ({ value: style, label: text.styles[style] }))}
            onChange={(style) =>
              onDraft({ ...draft, style, start: clampStart(style, draft.start) })
            }
          />

          <div className="grid gap-1">
            <CheckboxField
              label={text.bracketed}
              checked={draft.bracketed}
              disabled={noNumbers}
              onChange={(event) => onDraft({ ...draft, bracketed: event.target.checked })}
            />
            <p className="text-xs text-muted-foreground">{text.bracketedHint}</p>
          </div>

          <fieldset className="grid gap-2">
            <legend className="mb-1 text-sm font-medium">{text.skip}</legend>
            <div className="grid grid-cols-2 gap-2">
              {PAGE_KINDS.map((kind) => (
                <CheckboxField
                  key={kind}
                  label={MESSAGES.pages.kinds[kind]}
                  checked={draft.skipKinds.includes(kind)}
                  onChange={(event) => toggleKind(kind, event.target.checked)}
                />
              ))}
            </div>
            <p className="text-xs text-muted-foreground">{text.skipHint}</p>
          </fieldset>

          {preview.isError ? <ErrorAlert message={describeError(preview.error)} /> : null}

          {firstHidden === undefined ? null : (
            <div
              className="grid gap-2 rounded-md border border-status-attention bg-status-attention/10 p-3 text-sm"
              data-testid="hides-gap"
            >
              <p className="flex items-start gap-2">
                <TriangleAlertIcon
                  className="mt-0.5 size-4 shrink-0 text-status-attention"
                  aria-hidden="true"
                />
                <span>
                  <strong>{text.hides.title}</strong>{' '}
                  {hidden.length === 1
                    ? text.hides.text(
                        formatNumber(firstHidden.style, firstHidden.jumpFrom),
                        formatNumber(firstHidden.style, firstHidden.jumpTo),
                      )
                    : text.hides.textMany(hidden.length)}
                </span>
              </p>
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={adding}
                  onClick={() => onAddMissing(hidden)}
                >
                  {text.hides.addFirst}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    const end = endOfRunBefore(pages, firstHidden.beforePageId);
                    if (end !== null) {
                      onDraft({ ...draft, lastId: end.id });
                    }
                  }}
                >
                  {text.hides.twoRuns}
                </Button>
              </div>
            </div>
          )}
        </div>
      }
      footer={footer}
    />
  );
}
