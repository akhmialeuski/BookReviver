import { useState } from 'react';
import type { PageVersionSchema, ResultMark } from '@/api';
import { useMarkResult } from '@/features/processing/queries';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { ErrorAlert } from '@/shared/ui/error-alert';
import { TextareaField } from '@/shared/ui/textarea-field';

/**
 * The notes of the user on one result: a good or bad mark and a comment of one line or several.
 *
 * Both are saved at once and can be changed at any time, and the server keeps every change. Pressing the mark that is
 * set takes it off. The notes are no input of the step, so setting them makes no picture and changes no identifier.
 */

const labels = MESSAGES.processing.history;
const MARKS: readonly ResultMark[] = ['good', 'bad'];

export function ResultNote({
  projectId,
  version,
}: {
  projectId: string;
  version: PageVersionSchema;
}): React.JSX.Element {
  const save = useMarkResult(projectId);
  // The comment being written, or null while the stored one is shown
  const [draft, setDraft] = useState<string | null>(null);

  // Only the saving of a comment closes the field of the comment. A mark pressed while a comment is being written settles
  // later than the press, and its answer must not take the text away
  function send(mark: ResultMark | null, comment: string, closesDraft = false): void {
    save.mutate(
      {
        path: { project_id: projectId, page_id: version.page_id, version_id: version.id },
        body: { mark, comment },
      },
      closesDraft ? { onSuccess: () => setDraft(null) } : undefined,
    );
  }

  return (
    <div className="grid gap-1.5" data-testid="result-note" data-version={version.id}>
      <fieldset className="m-0 flex min-w-0 gap-1.5 border-0 p-0">
        <legend className="sr-only">{labels.mark.group}</legend>
        {MARKS.map((mark) => (
          <Button
            key={mark}
            variant={version.mark === mark ? 'secondary' : 'outline'}
            size="sm"
            aria-pressed={version.mark === mark}
            disabled={save.isPending}
            data-testid={`result-mark-${mark}`}
            data-version={version.id}
            onClick={() => send(version.mark === mark ? null : mark, version.comment)}
          >
            {labels.mark[mark]}
          </Button>
        ))}
      </fieldset>
      {draft === null ? (
        <>
          {version.comment === '' ? null : (
            <p className="break-words whitespace-pre-wrap" data-testid="result-comment">
              {version.comment}
            </p>
          )}
          <Button
            variant="link"
            size="sm"
            className="h-auto w-fit p-0"
            data-testid="result-comment-edit"
            data-version={version.id}
            onClick={() => setDraft(version.comment)}
          >
            {version.comment === '' ? labels.comment.add : labels.comment.edit}
          </Button>
        </>
      ) : (
        <div className="grid gap-1.5">
          <TextareaField
            label={labels.comment.label}
            value={draft}
            placeholder={labels.comment.placeholder}
            data-testid="result-comment-input"
            onChange={(event) => setDraft(event.target.value)}
          />
          <div className="flex gap-1.5">
            <Button
              size="sm"
              disabled={save.isPending}
              data-testid="result-comment-save"
              onClick={() => send(version.mark, draft, true)}
            >
              {save.isPending ? labels.comment.saving : labels.comment.save}
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={save.isPending}
              onClick={() => setDraft(null)}
            >
              {labels.comment.cancel}
            </Button>
          </div>
        </div>
      )}
      {save.error === null ? null : <ErrorAlert message={describeError(save.error)} />}
    </div>
  );
}
