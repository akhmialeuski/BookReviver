import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { listScansApiV1ProjectsProjectIdScansGetOptions } from '@/api/@tanstack/react-query.gen';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/ui/card';
import { ErrorAlert } from '@/shared/ui/error-alert';
import { Pager } from '@/shared/ui/pager';

/**
 * The scans of the book as a grid of thumbnails, all of them or those of one source. A scan whose images are not
 * cut yet shows a placeholder, and appears with its picture when the server reports it ready.
 */

const PAGE_SIZE = 24;

export function ScansPanel({
  projectId,
  sourceId,
  onClearSource,
}: {
  projectId: string;
  sourceId: string | null;
  onClearSource: () => void;
}): React.JSX.Element {
  const [pageState, setPageState] = useState({ sourceId, page: 1 });
  // A new filter starts at its first page; deriving it here avoids an effect that resets the page afterwards
  const page = pageState.sourceId === sourceId ? pageState.page : 1;
  const scans = useQuery({
    ...listScansApiV1ProjectsProjectIdScansGetOptions({
      path: { project_id: projectId },
      query: { page, size: PAGE_SIZE, source_id: sourceId },
    }),
    placeholderData: keepPreviousData,
  });

  let body: React.JSX.Element;
  if (scans.isError) {
    body = <ErrorAlert message={describeError(scans.error)} />;
  } else if (scans.data === undefined) {
    body = <p className="text-sm text-muted-foreground">{MESSAGES.common.loading}</p>;
  } else if (scans.data.items.length === 0) {
    body = <p className="text-sm text-muted-foreground">{MESSAGES.book.scans.empty}</p>;
  } else {
    body = (
      <>
        <ul className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
          {scans.data.items.map((scan, index) => {
            // The number of a scan restarts in every source, so a book of one image per file would show only "1"
            const ordinal = (scans.data.page - 1) * scans.data.size + index + 1;
            return (
              <li key={scan.id} className="grid gap-1 text-xs">
                <div className="flex aspect-[3/4] items-center justify-center overflow-hidden rounded-md border bg-muted">
                  {scan.images === null ? (
                    <span className="px-2 text-center text-muted-foreground">
                      {MESSAGES.book.scans.pending}
                    </span>
                  ) : (
                    <img
                      src={scan.images.thumbnail}
                      alt={MESSAGES.book.scans.label(ordinal)}
                      loading="lazy"
                      className="size-full object-contain"
                    />
                  )}
                </div>
                <span className="font-medium">
                  {MESSAGES.book.scans.label(ordinal)}
                  {scan.source_label === '' ? '' : ` (${scan.source_label})`}
                </span>
                <span className="text-muted-foreground">
                  {MESSAGES.book.scans.size(scan.facts.width_px, scan.facts.height_px)}
                </span>
              </li>
            );
          })}
        </ul>
        <Pager
          page={scans.data.page}
          pages={scans.data.pages}
          onPageChange={(next) => setPageState({ sourceId, page: next })}
        />
      </>
    );
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle>{MESSAGES.book.scans.title}</CardTitle>
        {sourceId === null ? null : (
          <Button variant="outline" size="sm" onClick={onClearSource}>
            {MESSAGES.book.scans.showAll}
          </Button>
        )}
      </CardHeader>
      <CardContent className="grid gap-4">{body}</CardContent>
    </Card>
  );
}
