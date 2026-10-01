import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CircleCheckIcon } from 'lucide-react';
import type { JobSchema } from '@/api';
import {
  cancelJobApiV1JobsJobIdDeleteMutation,
  readJobApiV1JobsJobIdGetOptions,
  readJobApiV1JobsJobIdGetQueryKey,
} from '@/api/@tanstack/react-query.gen';
import { isActiveJob, refreshProject } from '@/features/projects/events';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Alert, AlertDescription, AlertTitle } from '@/shared/ui/alert';
import { Badge } from '@/shared/ui/badge';
import { Button } from '@/shared/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/ui/card';
import { ErrorAlert } from '@/shared/ui/error-alert';
import { Progress } from '@/shared/ui/progress';

/**
 * The import of the last upload: its state and progress while it runs, and what became of every file when it ends.
 *
 * The job is a query that the event stream writes into, so the bar moves as events arrive. The query also polls
 * slowly while the job is active, a safety net for an event stream that was cut, so a finished import is never
 * left showing as running.
 */

const POLL_INTERVAL_MS = 5000;
const PERCENT = 100;

function Result({ job }: { job: JobSchema }): React.JSX.Element | null {
  const { result } = job;
  if (result === null) {
    return null;
  }
  const messages = MESSAGES.importJob;
  return (
    <div className="grid gap-3 text-sm">
      <p>
        {result.imported.length > 0
          ? messages.imported(result.imported.length)
          : messages.nothingImported}
      </p>
      {result.rejected.length === 0 ? null : (
        <Alert variant="destructive">
          <AlertTitle>{messages.rejectedTitle(result.rejected.length)}</AlertTitle>
          <AlertDescription>
            <ul className="grid gap-1" data-testid="rejected-files">
              {result.rejected.map((file) => (
                <li key={`${file.file_name}:${file.reason}`}>
                  <span className="font-medium break-all">{file.file_name}</span>
                  {': '}
                  {messages.reasons[file.reason]}
                  {file.detail === '' ? '' : ` (${file.detail})`}
                </li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}
      {result.skipped.length === 0 ? null : (
        <Alert>
          <AlertTitle>{messages.skippedTitle(result.skipped.length)}</AlertTitle>
          <AlertDescription>
            <ul className="grid gap-1" data-testid="skipped-by-cancel">
              {result.skipped.map((name) => (
                <li key={name} className="break-all">
                  {name}
                </li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}
    </div>
  );
}

export function ImportStatus({
  projectId,
  jobId,
  onDismiss,
}: {
  projectId: string;
  jobId: string;
  onDismiss: () => void;
}): React.JSX.Element {
  const queryClient = useQueryClient();
  const job = useQuery({
    ...readJobApiV1JobsJobIdGetOptions({ path: { job_id: jobId } }),
    refetchInterval: (query) => (isActiveJob(query.state.data) ? POLL_INTERVAL_MS : false),
  });
  const cancel = useMutation({
    ...cancelJobApiV1JobsJobIdDeleteMutation(),
    onSuccess: (cancelled) => {
      queryClient.setQueryData(
        readJobApiV1JobsJobIdGetQueryKey({ path: { job_id: jobId } }),
        cancelled,
      );
      void refreshProject(queryClient, projectId);
    },
  });

  if (job.isError) {
    return <ErrorAlert message={describeError(job.error)} />;
  }
  if (job.data === undefined) {
    return <p className="text-sm text-muted-foreground">{MESSAGES.common.loading}</p>;
  }
  const { data } = job;
  const active = isActiveJob(data);
  const percent = Math.round(data.progress.fraction * PERCENT);

  return (
    <Card data-testid="import-status">
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle className="flex items-center gap-2">
          {MESSAGES.importJob.title}
          <Badge
            variant={data.state === 'failed' ? 'destructive' : 'secondary'}
            data-testid="job-state"
          >
            {MESSAGES.importJob.states[data.state]}
          </Badge>
        </CardTitle>
        {active ? (
          <Button
            variant="outline"
            size="sm"
            disabled={cancel.isPending}
            onClick={() => cancel.mutate({ path: { job_id: jobId } })}
          >
            {cancel.isPending ? MESSAGES.importJob.cancelling : MESSAGES.importJob.cancel}
          </Button>
        ) : (
          <Button variant="ghost" size="sm" onClick={onDismiss}>
            {MESSAGES.importJob.dismiss}
          </Button>
        )}
      </CardHeader>
      <CardContent className="grid gap-4">
        {active ? (
          <div className="grid gap-2">
            <Progress value={percent} aria-label={MESSAGES.importJob.title} />
            <p className="text-xs text-muted-foreground" data-testid="job-progress">
              {MESSAGES.importJob.progress(data.progress.done, data.progress.total)}
            </p>
          </div>
        ) : null}
        {cancel.isError ? <ErrorAlert message={describeError(cancel.error)} /> : null}
        {data.state === 'failed' ? (
          <ErrorAlert message={data.error === '' ? MESSAGES.importJob.failedTitle : data.error} />
        ) : null}
        {data.state === 'succeeded' && data.result?.rejected.length === 0 ? (
          <CircleCheckIcon className="size-5 text-green-600" aria-hidden="true" />
        ) : null}
        <Result job={data} />
      </CardContent>
    </Card>
  );
}
