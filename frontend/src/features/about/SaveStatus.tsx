import { CircleAlertIcon, CircleCheckIcon, LoaderCircleIcon } from 'lucide-react';
import { formatSavedAt, type SaveState } from '@/features/about/autosave';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';

/**
 * The line under the form that says whether the changes are saved, in the words the person can act on.
 *
 * It stays at the bottom of the window, because the form is longer than the window and the person types far from
 * its end. The state is also an attribute, so a test waits for it without reading the words.
 */

export function SaveStatus({
  state,
  savedAt,
  error,
  onRetry,
}: {
  state: SaveState;
  savedAt: Date | null;
  /** Why the server refused the changes, while the state is `failed`. */
  error: string;
  onRetry: () => void;
}): React.JSX.Element {
  const messages = MESSAGES.about.status;
  let content: React.ReactNode;
  switch (state) {
    case 'saving':
      content = (
        <>
          <LoaderCircleIcon className="size-4 animate-spin" />
          {messages.saving}
        </>
      );
      break;
    case 'saved':
      content = (
        <>
          <CircleCheckIcon className="size-4 text-status-done" />
          {messages.saved(savedAt === null ? '' : formatSavedAt(savedAt))}
        </>
      );
      break;
    case 'failed':
      content = (
        <>
          <CircleAlertIcon className="size-4 text-destructive" />
          <span className="text-destructive">{messages.failed(error)}</span>
          <Button type="button" variant="outline" size="sm" onClick={onRetry}>
            {messages.retry}
          </Button>
        </>
      );
      break;
    case 'invalid':
      content = (
        <>
          <CircleAlertIcon className="size-4 text-status-attention" />
          {messages.invalid}
        </>
      );
      break;
    default:
      content = messages.idle;
  }
  return (
    <div className="sticky bottom-0 -mx-6 border-t bg-background/95 px-6 py-3 backdrop-blur">
      <p
        role="status"
        data-testid="autosave-status"
        data-state={state}
        className="flex items-center gap-2 text-sm text-muted-foreground"
      >
        {content}
      </p>
    </div>
  );
}
