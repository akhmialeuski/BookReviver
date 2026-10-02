import type { JobSchema } from '@/api';
import { MESSAGES } from '@/shared/messages';
import { Alert, AlertDescription, AlertTitle } from '@/shared/ui/alert';
import { Badge } from '@/shared/ui/badge';
import { Button } from '@/shared/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/ui/card';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * What became of the files of an import that has ended: how many were taken in, which were rejected and why, which a
 * cancelled import never reached, and the reason of an import that failed.
 *
 * The Import stage shows it under the list of files for the latest import that has something to tell, and the person
 * closes it. The progress of an import that still runs is not here, it is in the row of the list.
 */

export function ImportStatus({
  job,
  onDismiss,
}: {
  job: JobSchema;
  onDismiss: () => void;
}): React.JSX.Element {
  const messages = MESSAGES.importJob;
  const { result } = job;

  return (
    <Card data-testid="import-status">
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle className="flex items-center gap-2">
          {messages.title}
          <Badge
            variant={job.state === 'failed' ? 'destructive' : 'secondary'}
            data-testid="job-state"
          >
            {messages.states[job.state]}
          </Badge>
        </CardTitle>
        <Button variant="ghost" size="sm" onClick={onDismiss}>
          {messages.dismiss}
        </Button>
      </CardHeader>
      <CardContent className="grid gap-3 text-sm">
        {job.state === 'failed' ? (
          <ErrorAlert message={job.error === '' ? messages.failedTitle : job.error} />
        ) : null}
        {result === null ? null : (
          <>
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
          </>
        )}
      </CardContent>
    </Card>
  );
}
