import type { JobSchema, SourceSchema } from '@/api';
import { MESSAGES } from '@/shared/messages';
import { Alert, AlertDescription, AlertTitle } from '@/shared/ui/alert';
import { Badge } from '@/shared/ui/badge';
import { Button } from '@/shared/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/ui/card';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * What became of the files of the imports that have ended: which were rejected and why, which a cancelled import never
 * reached, and the reason of an import that failed.
 *
 * The Import stage shows it under the list of files while there is something to tell, and the person closes it, which
 * deletes the rejected and skipped files on the server. The files that were taken in are the list above it, and the
 * progress of an import that still runs is in a row of that list.
 */

export function ImportStatus({
  failed,
  files,
  closing,
  onDismiss,
}: {
  /** The latest import that failed, or undefined when none did. */
  failed: JobSchema | undefined;
  /** The files that were rejected or skipped, in the order they were recorded. */
  files: readonly SourceSchema[];
  /** Whether the server is deleting the files, which a second press must not start again. */
  closing: boolean;
  onDismiss: () => void;
}): React.JSX.Element {
  const messages = MESSAGES.importJob;
  const rejected = files.filter((file) => file.status === 'rejected');
  const skipped = files.filter((file) => file.status === 'skipped');

  return (
    <Card data-testid="import-status">
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle className="flex items-center gap-2">
          {messages.title}
          {failed === undefined ? null : (
            <Badge variant="destructive" data-testid="job-state">
              {messages.states.failed}
            </Badge>
          )}
        </CardTitle>
        <Button variant="ghost" size="sm" disabled={closing} onClick={onDismiss}>
          {messages.dismiss}
        </Button>
      </CardHeader>
      <CardContent className="grid gap-3 text-sm">
        {failed === undefined ? null : (
          <ErrorAlert message={failed.error === '' ? messages.failedTitle : failed.error} />
        )}
        {rejected.length === 0 ? null : (
          <Alert variant="destructive">
            <AlertTitle>{messages.rejectedTitle(rejected.length)}</AlertTitle>
            <AlertDescription>
              <ul className="grid gap-1" data-testid="rejected-files">
                {rejected.map((file) => (
                  <li key={file.id}>
                    <span className="font-medium break-all">{file.file_name}</span>
                    {': '}
                    {file.rejection === null ? '' : messages.reasons[file.rejection]}
                    {file.detail === '' ? '' : ` (${file.detail})`}
                  </li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        )}
        {skipped.length === 0 ? null : (
          <Alert>
            <AlertTitle>{messages.skippedTitle(skipped.length)}</AlertTitle>
            <AlertDescription>
              <ul className="grid gap-1" data-testid="skipped-by-cancel">
                {skipped.map((file) => (
                  <li key={file.id} className="break-all">
                    {file.file_name}
                  </li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        )}
      </CardContent>
    </Card>
  );
}
