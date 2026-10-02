import { useMutation, useQueryClient } from '@tanstack/react-query';
import { FileImageIcon, FileTextIcon, Loader2Icon } from 'lucide-react';
import type { JobSchema, SourceSchema } from '@/api';
import { cancelJobApiV1JobsJobIdDeleteMutation } from '@/api/@tanstack/react-query.gen';
import { refreshProject } from '@/features/projects/events';
import { UploadDialog } from '@/features/projects/upload/UploadDialog';
import { describeError } from '@/shared/http/problem';
import { formatBytes, formatDateTime } from '@/shared/lib/format';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';
import { Badge } from '@/shared/ui/badge';
import { Button } from '@/shared/ui/button';
import { ErrorAlert } from '@/shared/ui/error-alert';
import { Progress } from '@/shared/ui/progress';

/**
 * The files of the book, one row each in the order of the book, and after them one row for every import that still
 * runs, with its progress bar and a button that stops it.
 *
 * A file is chosen by its row, and the row of the chosen file is outlined. An import does not know the names of its
 * files before it has taken them in, so its row speaks of the upload as a whole, and the files it has finished
 * appear above it as their own rows.
 */

const PERCENT = 100;

function FileRow({
  source,
  selected,
  onSelect,
}: {
  source: SourceSchema;
  selected: boolean;
  onSelect: () => void;
}): React.JSX.Element {
  const Icon = source.kind === 'image' ? FileImageIcon : FileTextIcon;
  const detail = [
    MESSAGES.import.files.kinds[source.file_type],
    MESSAGES.book.sources.scans(source.scan_count),
    formatBytes(source.size_bytes),
    MESSAGES.book.sources.imported(formatDateTime(source.imported_at)),
  ].join(' · ');

  return (
    <li className="[contain-intrinsic-size:auto_4.5rem] [content-visibility:auto]">
      <button
        type="button"
        aria-pressed={selected}
        data-testid="source-row"
        onClick={onSelect}
        className={cn(
          'flex w-full items-center gap-3 rounded-xl border bg-card p-3 text-left outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50',
          selected ? 'border-primary ring-1 ring-primary' : 'hover:bg-accent/40',
        )}
      >
        <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-muted">
          <Icon className="size-5 text-muted-foreground" aria-hidden="true" />
        </span>
        <span className="grid min-w-0 flex-1">
          <span className="truncate font-medium" title={source.file_name} data-testid="source-name">
            {source.file_name}
          </span>
          <span className="truncate text-xs text-muted-foreground">{detail}</span>
        </span>
        <Badge variant="secondary" className="text-status-done">
          {MESSAGES.import.files.done}
        </Badge>
      </button>
    </li>
  );
}

function ImportRow({ job, projectId }: { job: JobSchema; projectId: string }): React.JSX.Element {
  const queryClient = useQueryClient();
  const stop = useMutation({
    ...cancelJobApiV1JobsJobIdDeleteMutation(),
    onSuccess: () => refreshProject(queryClient, projectId),
  });
  const labels = MESSAGES.import.files;
  const { done, total, fraction } = job.progress;

  return (
    <li className="grid gap-2 rounded-xl border bg-card p-3" data-testid="import-row">
      <div className="flex items-center gap-3">
        <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-muted">
          <Loader2Icon
            className="size-5 text-status-running motion-safe:animate-spin"
            aria-hidden="true"
          />
        </span>
        <span className="min-w-0 flex-1 truncate font-medium">{labels.importing}</span>
        <Badge variant="secondary" className="text-status-running" data-testid="job-state">
          {job.state === 'queued' ? labels.waiting : MESSAGES.importJob.states.running}
        </Badge>
        <Button
          variant="ghost"
          size="sm"
          disabled={stop.isPending}
          onClick={() => stop.mutate({ path: { job_id: job.id } })}
        >
          {stop.isPending ? labels.stopping : labels.stop}
        </Button>
      </div>
      <div className="flex items-center gap-3">
        <Progress
          value={Math.round(fraction * PERCENT)}
          aria-label={labels.importing}
          className="flex-1"
        />
        <span
          className="text-xs whitespace-nowrap text-muted-foreground"
          data-testid="job-progress"
        >
          {labels.progress(done, total)}
        </span>
      </div>
      {stop.isError ? <ErrorAlert message={describeError(stop.error)} /> : null}
    </li>
  );
}

export function FileList({
  projectId,
  sources,
  imports,
  selectedId,
  onSelect,
}: {
  projectId: string;
  sources: readonly SourceSchema[];
  /** The imports that are queued or running. */
  imports: readonly JobSchema[];
  selectedId: string | undefined;
  onSelect: (sourceId: string) => void;
}): React.JSX.Element {
  const scans = sources.reduce((sum, source) => sum + source.scan_count, 0);
  const size = formatBytes(sources.reduce((sum, source) => sum + source.size_bytes, 0));

  return (
    <section className="grid gap-3" aria-labelledby="import-files-title">
      <div className="flex items-end justify-between gap-4">
        <div className="grid">
          <h3 id="import-files-title" className="text-lg font-semibold">
            {MESSAGES.import.files.title}
          </h3>
          <p className="text-sm text-muted-foreground" data-testid="files-summary">
            {MESSAGES.import.files.summary(sources.length, scans, size)}
          </p>
        </div>
        <UploadDialog projectId={projectId} />
      </div>
      <ul className="grid max-h-[40vh] gap-2 overflow-y-auto" data-testid="source-list">
        {sources.map((source) => (
          <FileRow
            key={source.id}
            source={source}
            selected={source.id === selectedId}
            onSelect={() => onSelect(source.id)}
          />
        ))}
        {imports.map((job) => (
          <ImportRow key={job.id} job={job} projectId={projectId} />
        ))}
      </ul>
    </section>
  );
}
