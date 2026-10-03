import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import type { PageSchema } from '@/api';
import {
  listScansApiV1ProjectsProjectIdScansGetOptions,
  listSourcesApiV1ProjectsProjectIdSourcesGetOptions,
} from '@/api/@tanstack/react-query.gen';
import { useAttachScan } from '@/features/pages/actions';
import { describePageError } from '@/features/pages/errors';
import { describeError } from '@/shared/http/problem';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { CheckboxField } from '@/shared/ui/checkbox-field';
import { DialogFooter } from '@/shared/ui/dialog';
import { ErrorAlert } from '@/shared/ui/error-alert';
import { Pager } from '@/shared/ui/pager';
import { SelectField } from '@/shared/ui/select-field';

/**
 * The step of the page dialog that binds a scan to a placeholder: choose a file, choose one of its scans by its
 * picture, and say whether to take the scan from the page that already shows it.
 *
 * The scans of a file are listed a screenful at a time, as the scans panel lists them. A scan the import already
 * made into a page of its own can be bound only by taking it over, which deletes that page, so the choice is
 * explicit; without it the server answers a conflict that names the pages showing the scan.
 */

const SOURCES_SIZE = 100;
const SCANS_SIZE = 12;

export function AttachScanView({
  projectId,
  page,
  onBack,
  onDone,
}: {
  projectId: string;
  page: PageSchema;
  onBack: () => void;
  onDone: () => void;
}): React.JSX.Element {
  const attach = useAttachScan(projectId);
  const [chosenSourceId, setSourceId] = useState<string | null>(null);
  const [scanPage, setScanPage] = useState(1);
  const [scanId, setScanId] = useState<string | null>(null);
  const [takeOver, setTakeOver] = useState(false);

  const sources = useQuery(
    listSourcesApiV1ProjectsProjectIdSourcesGetOptions({
      path: { project_id: projectId },
      query: { page: 1, size: SOURCES_SIZE },
    }),
  );
  const sourceId = chosenSourceId ?? sources.data?.items[0]?.id ?? null;
  const scans = useQuery({
    ...listScansApiV1ProjectsProjectIdScansGetOptions({
      path: { project_id: projectId },
      query: { page: scanPage, size: SCANS_SIZE, source_id: sourceId },
    }),
    enabled: sourceId !== null,
    placeholderData: keepPreviousData,
  });

  if (sources.isError) {
    return <ErrorAlert message={describeError(sources.error)} />;
  }
  if (sources.data !== undefined && sources.data.items.length === 0) {
    return (
      <div className="grid gap-4">
        <p className="text-sm text-muted-foreground">{MESSAGES.pages.attach.noSources}</p>
        <DialogFooter>
          <Button variant="outline" onClick={onBack}>
            {MESSAGES.pages.attach.back}
          </Button>
        </DialogFooter>
      </div>
    );
  }

  return (
    <form
      className="grid gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (scanId !== null) {
          attach.mutate(
            {
              path: { project_id: projectId, page_id: page.id },
              body: { scan_id: scanId, take_over: takeOver },
            },
            { onSuccess: onDone },
          );
        }
      }}
    >
      <SelectField
        label={MESSAGES.pages.attach.source}
        value={sourceId ?? ''}
        onChange={(event) => {
          setSourceId(event.target.value);
          setScanPage(1);
          setScanId(null);
        }}
      >
        {(sources.data?.items ?? []).map((source) => (
          <option key={source.id} value={source.id}>
            {source.file_name}
          </option>
        ))}
      </SelectField>

      {scans.isError ? <ErrorAlert message={describeError(scans.error)} /> : null}
      {scans.data !== undefined && scans.data.items.length === 0 ? (
        <p className="text-sm text-muted-foreground">{MESSAGES.pages.attach.noScans}</p>
      ) : null}
      <ul className="grid grid-cols-3 gap-2 sm:grid-cols-4">
        {(scans.data?.items ?? []).map((scan) => (
          <li key={scan.id}>
            <button
              type="button"
              aria-pressed={scanId === scan.id}
              // A scan without pictures is not cut yet, and the server refuses to bind it
              disabled={scan.images === null}
              onClick={() => setScanId(scan.id)}
              className={cn(
                'grid w-full gap-1 rounded-md border p-1 text-xs outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-60',
                scanId === scan.id ? 'border-primary ring-2 ring-primary' : 'hover:bg-accent/50',
              )}
            >
              <span className="flex aspect-[3/4] items-center justify-center overflow-hidden rounded bg-muted">
                {scan.images === null ? (
                  <span className="px-1 text-center text-muted-foreground">
                    {MESSAGES.book.scans.pending}
                  </span>
                ) : (
                  <img
                    src={scan.images.thumbnail}
                    alt=""
                    loading="lazy"
                    className="size-full object-contain"
                  />
                )}
              </span>
              <span className="truncate text-center">
                {MESSAGES.pages.attach.scan(scan.number + 1, scan.source_label)}
              </span>
            </button>
          </li>
        ))}
      </ul>
      {scans.data === undefined ? null : (
        <Pager page={scans.data.page} pages={scans.data.pages} onPageChange={setScanPage} />
      )}

      <CheckboxField
        label={MESSAGES.pages.attach.takeOver}
        checked={takeOver}
        onChange={(event) => setTakeOver(event.target.checked)}
      />
      <p className="-mt-2 text-xs text-muted-foreground">{MESSAGES.pages.attach.takeOverHint}</p>

      {attach.isError ? <ErrorAlert message={describePageError(attach.error)} /> : null}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onBack}>
          {MESSAGES.pages.attach.back}
        </Button>
        <Button type="submit" disabled={scanId === null || attach.isPending}>
          {attach.isPending ? MESSAGES.pages.attach.submitting : MESSAGES.pages.attach.submit}
        </Button>
      </DialogFooter>
    </form>
  );
}
