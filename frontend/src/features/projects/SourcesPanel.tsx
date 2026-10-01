import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { BookOpenIcon, FileIcon, MoveIcon } from 'lucide-react';
import { useState } from 'react';
import type { SourceSchema } from '@/api';
import { listSourcesApiV1ProjectsProjectIdSourcesGetOptions } from '@/api/@tanstack/react-query.gen';
import { MovePagesDialog } from '@/features/pages/MovePagesDialog';
import { useManifest } from '@/features/pages/manifest';
import { firstPageOfSource, pageIdsOfSource } from '@/features/pages/order';
import { DeleteSourceDialog } from '@/features/projects/DeleteSourceDialog';
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
 * scans panel to the scans of that file, open the pages cut from it in the viewer, move all those pages to another
 * place in the book with one action, or delete the file after a confirmation.
 */

const PAGE_SIZE = 20;

function SourceRow({
  projectId,
  source,
  selected,
  firstPageId,
  onSelect,
  onMovePages,
}: {
  projectId: string;
  source: SourceSchema;
  selected: boolean;
  /** The first page of the book cut from this file, or undefined when no page stands for it. */
  firstPageId: string | undefined;
  onSelect: () => void;
  onMovePages: () => void;
}): React.JSX.Element {
  return (
    <li className="flex flex-wrap items-center gap-x-4 gap-y-1 py-2 text-sm">
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
        variant={selected ? 'secondary' : 'ghost'}
        size="sm"
        aria-pressed={selected}
        onClick={onSelect}
      >
        {MESSAGES.book.sources.showScans}
      </Button>
      {firstPageId === undefined ? null : (
        <>
          <Button
            asChild
            variant="ghost"
            size="sm"
            aria-label={MESSAGES.book.sources.viewPages(source.file_name)}
          >
            <Link
              to="/projects/$projectId/viewer"
              params={{ projectId }}
              search={{ page: firstPageId }}
            >
              <BookOpenIcon />
              {MESSAGES.book.sources.viewPagesShort}
            </Link>
          </Button>
          <Button
            variant="ghost"
            size="sm"
            aria-label={MESSAGES.book.sources.movePages(source.file_name)}
            onClick={onMovePages}
          >
            <MoveIcon />
            {MESSAGES.book.sources.movePagesShort}
          </Button>
        </>
      )}
      <DeleteSourceDialog projectId={projectId} source={source} />
    </li>
  );
}

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
  const [movingSource, setMovingSource] = useState<SourceSchema | null>(null);
  const sources = useQuery({
    ...listSourcesApiV1ProjectsProjectIdSourcesGetOptions({
      path: { project_id: projectId },
      query: { page, size: PAGE_SIZE },
    }),
    placeholderData: keepPreviousData,
  });
  const pages = useManifest(projectId).data ?? [];

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
            <SourceRow
              key={source.id}
              projectId={projectId}
              source={source}
              selected={selectedSourceId === source.id}
              firstPageId={firstPageOfSource(pages, source.id)?.id}
              onSelect={() => onSelectSource(selectedSourceId === source.id ? null : source.id)}
              onMovePages={() => setMovingSource(source)}
            />
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
      <MovePagesDialog
        projectId={projectId}
        pages={pages}
        target={
          movingSource === null
            ? null
            : {
                pageIds: pageIdsOfSource(pages, movingSource.id),
                sourceId: movingSource.id,
                sourceName: movingSource.file_name,
              }
        }
        onClose={() => setMovingSource(null)}
      />
    </Card>
  );
}
