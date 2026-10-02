import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ActivityIcon, AlertCircleIcon, CheckCircle2Icon, Loader2Icon, XIcon } from 'lucide-react';
import { useState } from 'react';
import type { JobSchema } from '@/api';
import { cancelJobApiV1JobsJobIdDeleteMutation } from '@/api/@tanstack/react-query.gen';
import { invalidateJobs } from '@/features/projects/queries';
import {
  formatTime,
  isStoppable,
  jobLabel,
  jobMoment,
  latestActiveJob,
} from '@/features/workspace/jobs';
import { useActiveJobs, useRecentJobs } from '@/features/workspace/queries';
import { describeError } from '@/shared/http/problem';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { ErrorAlert } from '@/shared/ui/error-alert';
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/ui/popover';
import { Progress } from '@/shared/ui/progress';

/**
 * The chip in the header that shows the most recent running job of the book and opens the list of its jobs.
 *
 * The chip reads the jobs that are queued or running, and the list reads the latest jobs of any state only while it is
 * open. Both follow the `job-changed` events of the book. A job that is still queued or running has a Stop button,
 * which cancels it through the jobs route.
 */

function JobRow({ job, projectId }: { job: JobSchema; projectId: string }): React.JSX.Element {
  const queryClient = useQueryClient();
  const stop = useMutation({
    ...cancelJobApiV1JobsJobIdDeleteMutation(),
    onSuccess: () => invalidateJobs(queryClient, projectId),
  });
  const labels = MESSAGES.activity;
  const time = formatTime(jobMoment(job));
  const percent = Math.round(job.progress.fraction * 100);

  return (
    <li className="grid gap-1.5 px-4 py-3" data-testid="activity-job" data-state={job.state}>
      <div className="flex items-start gap-3">
        <span className="mt-0.5" aria-hidden="true">
          {job.state === 'running' || job.state === 'queued' ? (
            <Loader2Icon className="size-4 text-status-running motion-safe:animate-spin" />
          ) : job.state === 'succeeded' ? (
            <CheckCircle2Icon className="size-4 text-status-done" />
          ) : (
            <AlertCircleIcon
              className={cn(
                'size-4',
                job.state === 'failed' ? 'text-status-failed' : 'text-muted-foreground',
              )}
            />
          )}
        </span>
        <div className="grid min-w-0 flex-1 gap-0.5">
          <p className="truncate text-sm font-medium">{jobLabel(job)}</p>
          <p className="text-xs text-muted-foreground">
            {labels.detail(job.state, time, job.progress)}
          </p>
          {job.state === 'failed' && job.error !== '' ? (
            <p className="text-xs text-status-failed">{job.error}</p>
          ) : null}
        </div>
        {isStoppable(job) ? (
          <Button
            variant="ghost"
            size="sm"
            disabled={stop.isPending}
            onClick={() => stop.mutate({ path: { job_id: job.id } })}
          >
            <XIcon />
            {labels.stop}
          </Button>
        ) : null}
      </div>
      {isStoppable(job) && job.progress.total > 0 ? (
        <Progress value={percent} aria-label={jobLabel(job)} className="ml-7 w-auto" />
      ) : null}
      {stop.isError ? <ErrorAlert message={describeError(stop.error)} /> : null}
    </li>
  );
}

export function ActivityChip({ projectId }: { projectId: string }): React.JSX.Element {
  const [open, setOpen] = useState(false);
  const active = useActiveJobs(projectId);
  const recent = useRecentJobs(projectId, open);
  const labels = MESSAGES.activity;
  const current = latestActiveJob(active.data ?? []);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant={current === undefined ? 'ghost' : 'secondary'}
          size="sm"
          data-testid="activity-chip"
          data-busy={current === undefined ? 'false' : 'true'}
          className={cn(current !== undefined && 'text-status-running')}
        >
          {current === undefined ? (
            <ActivityIcon />
          ) : (
            <Loader2Icon className="motion-safe:animate-spin" />
          )}
          {current === undefined ? labels.idle : labels.chip(jobLabel(current), current.progress)}
        </Button>
      </PopoverTrigger>
      <PopoverContent>
        <div className="flex items-baseline justify-between border-b px-4 py-3">
          <h2 className="text-sm font-semibold">{labels.title}</h2>
          <span className="text-xs text-muted-foreground">{labels.thisBook}</span>
        </div>
        {recent.isError ? (
          <div className="p-4">
            <ErrorAlert message={describeError(recent.error)} />
          </div>
        ) : recent.data === undefined ? (
          <p className="p-4 text-sm text-muted-foreground">{MESSAGES.common.loading}</p>
        ) : recent.data.length === 0 ? (
          <p className="p-4 text-sm text-muted-foreground">{labels.empty}</p>
        ) : (
          <ul className="max-h-96 divide-y overflow-y-auto">
            {recent.data.map((job) => (
              <JobRow key={job.id} job={job} projectId={projectId} />
            ))}
          </ul>
        )}
      </PopoverContent>
    </Popover>
  );
}
