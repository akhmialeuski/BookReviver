import { LoaderCircleIcon, ScanSearchIcon } from 'lucide-react';
import { useState } from 'react';
import type { ContentType, PageSchema } from '@/api';
import { commonOf } from '@/features/order/summary';
import { useUpdatePages } from '@/features/pages/actions';
import { describePageError } from '@/features/pages/errors';
import { shortName } from '@/features/pages/names';
import { useDetectContent } from '@/features/processing/queries';
import { pagesToChange, sourcesOf } from '@/features/workspace/content';
import { useActiveJobs } from '@/features/workspace/queries';
import type { StripItem } from '@/features/workspace/strip';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { ErrorAlert } from '@/shared/ui/error-alert';
import { SelectField } from '@/shared/ui/select-field';

/**
 * What the pages show, and the way to change it for the selected pages at once.
 *
 * The steps of a recipe have a condition, the pages of text or the pictures, and a page meets it by what it shows. The
 * program proposes the type from the share of the page that pictures cover and from the colour of the pictures, and the
 * reader's choice here is kept, so a later detection leaves the page as it is. The choice is made on the selected
 * pages, or on the open page when none is selected, and "Detect again" gives those pages back to the program.
 */

const labels = MESSAGES.processing.content;
const CONTENT_TYPES = Object.keys(MESSAGES.pages.contentTypes) as ContentType[];

/** Narrow the text of a field to a content type, or undefined when it is none. */
function asContentType(value: string): ContentType | undefined {
  return CONTENT_TYPES.find((type) => type === value);
}

export function ContentTypeSection({
  projectId,
  items,
  currentId,
  selected,
}: {
  projectId: string;
  /** Every page of the book with where it stands in the stage. */
  items: readonly StripItem[];
  /** The open page. */
  currentId: string | undefined;
  /** The pages selected in the grid. */
  selected: ReadonlySet<string>;
}): React.JSX.Element | null {
  const update = useUpdatePages(projectId);
  const detect = useDetectContent(projectId);
  const activeJobs = useActiveJobs(projectId);
  // The choice made and not yet settled, which the field shows at once, since a field that waits for the manifest to be
  // read again jumps back to its old value for a moment
  const [pending, setPending] = useState<{ ids: string; type: ContentType } | null>(null);
  const pages: PageSchema[] = pagesToChange(items, selected, currentId);
  if (pages.length === 0) {
    return null;
  }
  const ids = pages.map((page) => page.id);
  const key = ids.join(',');
  const shown =
    pending?.ids === key ? pending.type : commonOf(pages.map((page) => page.content_type));
  const { found, hand } = sourcesOf(pages);
  const detecting = detect.isPending || (activeJobs.data?.length ?? 0) > 0;
  const [first] = pages;

  return (
    <section className="grid gap-2" aria-label={labels.title} data-testid="content-type">
      <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
        {labels.title}
      </h3>
      <p className="text-xs text-muted-foreground">{labels.hint}</p>
      <p className="text-sm font-medium" data-testid="content-type-pages">
        {first !== undefined && pages.length === 1 ? shortName(first) : labels.pages(pages.length)}
      </p>
      <SelectField
        label={labels.label}
        value={shown ?? ''}
        data-testid="content-type-select"
        onChange={(event) => {
          const chosen = asContentType(event.target.value);
          if (chosen !== undefined) {
            setPending({ ids: key, type: chosen });
            update.mutate(
              { pageIds: ids, changes: { content_type: chosen } },
              { onSettled: () => setPending(null) },
            );
          }
        }}
      >
        {shown === null ? (
          <option value="" disabled>
            {labels.mixed}
          </option>
        ) : null}
        {CONTENT_TYPES.map((type) => (
          <option key={type} value={type}>
            {MESSAGES.pages.contentTypes[type]}
          </option>
        ))}
      </SelectField>
      <p className="text-xs text-muted-foreground" data-testid="content-type-sources">
        {labels.sources(found, hand)}
      </p>
      <Button
        variant="outline"
        size="sm"
        className="w-fit"
        disabled={detecting}
        title={labels.detectHint}
        data-testid="content-type-detect"
        onClick={() => detect.mutate({ path: { project_id: projectId }, body: { page_ids: ids } })}
      >
        {detecting ? <LoaderCircleIcon className="animate-spin" /> : <ScanSearchIcon />}
        {detecting ? labels.detecting : labels.detect}
      </Button>
      {update.isError ? <ErrorAlert message={describePageError(update.error)} /> : null}
      {detect.isError ? <ErrorAlert message={describeError(detect.error)} /> : null}
    </section>
  );
}
