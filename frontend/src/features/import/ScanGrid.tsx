import type { PageScanSchema, SourceSchema } from '@/api';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { ErrorAlert } from '@/shared/ui/error-alert';
import { Pager } from '@/shared/ui/pager';

/**
 * The scans of the chosen file as a grid of thumbnails, a page of the list at a time.
 *
 * A scan whose images are not cut yet shows a placeholder and gets its picture when the server reports it ready. A
 * click on a scan asks for it to be opened large on the canvas, which the stage answers.
 */

export function ScanGrid({
  file,
  scans,
  error,
  onPage,
  onOpen,
}: {
  file: SourceSchema;
  /** The page of scans to show, or undefined while it is being read. */
  scans: PageScanSchema | undefined;
  /** Why the page could not be read, as the query reports it, or null. */
  error: unknown;
  onPage: (page: number) => void;
  onOpen: (scanId: string) => void;
}): React.JSX.Element {
  let body: React.JSX.Element;
  if (error !== null) {
    body = <ErrorAlert message={describeError(error)} />;
  } else if (scans === undefined) {
    body = <p className="text-sm text-muted-foreground">{MESSAGES.common.loading}</p>;
  } else if (scans.items.length === 0) {
    body = <p className="text-sm text-muted-foreground">{MESSAGES.book.scans.empty}</p>;
  } else {
    body = (
      <>
        <ul
          className="grid grid-cols-[repeat(auto-fill,minmax(6.5rem,1fr))] gap-4"
          data-testid="scan-grid"
        >
          {scans.items.map((scan) => {
            // The number counts from 0 in the file, and the reader counts from 1
            const label = MESSAGES.book.scans.label(scan.number + 1);
            return (
              <li key={scan.id}>
                <button
                  type="button"
                  data-testid="scan-tile"
                  onClick={() => onOpen(scan.id)}
                  className="grid w-full gap-1 rounded-md text-xs outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
                >
                  <span className="flex aspect-[3/4] items-center justify-center overflow-hidden rounded-md border bg-muted">
                    {scan.images === null ? (
                      <span className="px-2 text-center text-muted-foreground">
                        {MESSAGES.book.scans.pending}
                      </span>
                    ) : (
                      <img
                        src={scan.images.thumbnail}
                        alt={label}
                        loading="lazy"
                        className="size-full object-contain"
                      />
                    )}
                  </span>
                  <span className="text-center font-medium">
                    {label}
                    {scan.source_label === '' ? '' : ` (${scan.source_label})`}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
        <Pager page={scans.page} pages={scans.pages} onPageChange={onPage} />
      </>
    );
  }

  return (
    <section className="grid gap-3" aria-labelledby="import-scans-title">
      <div className="flex items-baseline justify-between gap-4">
        <h3 id="import-scans-title" className="text-base font-semibold">
          {MESSAGES.import.scans.title(file.file_name)}
          <span className="ml-2 text-sm font-normal text-muted-foreground">{file.scan_count}</span>
        </h3>
        <p className="text-xs text-muted-foreground">{MESSAGES.import.scans.hint}</p>
      </div>
      {body}
    </section>
  );
}
