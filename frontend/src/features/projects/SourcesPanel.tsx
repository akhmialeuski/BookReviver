import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { FileIcon } from 'lucide-react';
import { useState } from 'react';
import { listSourcesApiV1ProjectsProjectIdSourcesGetOptions } from '@/api/@tanstack/react-query.gen';
import { describeError } from '@/shared/http/problem';
import { formatBytes, formatDate } from '@/shared/lib/format';
import { MESSAGES } from '@/shared/messages';
import { Badge } from '@/shared/ui/badge';
import { Button } from '@/shared/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/ui/card';
import { ErrorAlert } from '@/shared/ui/error-alert';
import { Pager } from '@/shared/ui/pager';

/**
 * The files the book was assembled from, one row each, with the number of scans a file gave. A row can narrow the
 * scans panel to the scans of that file.
 */

const PAGE_SIZE = 20;

export function SourcesPanel({
  projectId,
  selectedSourceId,
  onSelectSource,
}: {
  projectId: string;
  selectedSourceId: string | null;
  onSelectSource: (sourceId: string | null) => void;
}): React.JSX.Element {
  const [page, setPage] = useState(1);
  const sources = useQuery({
    ...listSourcesApiV1ProjectsProjectIdSourcesGetOptions({
      path: { project_id: projectId },
      query: { page, size: PAGE_SIZE },
    }),
    placeholderData: keepPreviousData,
  });

  let body: React.JSX.Element;
  if (sources.isError) {
    body = <ErrorAlert message={describeError(sources.error)} />;
  } else if (sources.data === undefined) {
    body = <p className="text-sm text-muted-foreground">{MESSAGES.common.loading}</p>;
  } else if (sources.data.items.length === 0) {
    body = <p className="text-sm text-muted-foreground">{MESSAGES.book.sources.empty}</p>;
  } else {
    body = (
      <>
        <ul className="divide-y">
          {sources.data.items.map((source) => (
            <li
              key={source.id}
              className="flex flex-wrap items-center gap-x-4 gap-y-1 py-2 text-sm"
            >
              <FileIcon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
              <span className="min-w-0 flex-1 break-all font-medium" data-testid="source-name">
                {source.file_name}
              </span>
              <Badge variant="outline">{MESSAGES.upload.kinds[source.file_type]}</Badge>
              <span className="text-muted-foreground">{formatBytes(source.size_bytes)}</span>
              <span className="text-muted-foreground">
                {MESSAGES.book.sources.scans(source.scan_count)}
              </span>
              <span className="text-muted-foreground">
                {MESSAGES.book.sources.imported(formatDate(source.imported_at))}
              </span>
              <Button
                variant={selectedSourceId === source.id ? 'secondary' : 'ghost'}
                size="sm"
                aria-pressed={selectedSourceId === source.id}
                onClick={() => onSelectSource(selectedSourceId === source.id ? null : source.id)}
              >
                {MESSAGES.book.sources.showScans}
              </Button>
            </li>
          ))}
        </ul>
        <Pager page={sources.data.page} pages={sources.data.pages} onPageChange={setPage} />
      </>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{MESSAGES.book.sources.title}</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4">{body}</CardContent>
    </Card>
  );
}
