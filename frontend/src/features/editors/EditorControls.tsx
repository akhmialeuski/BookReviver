import { HandIcon, LoaderCircleIcon } from 'lucide-react';
import type { EditorSession } from '@/features/editors/session';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The controls of the page editor in the panel: the part the editor itself puts there, such as the field of the angle, and
 * the button that opens the editor on the page. "Auto", which takes the manual edit away and lets the step find the
 * result again, stands on the toolbar of the canvas.
 */

const labels = MESSAGES.editors;

export function EditorControls({ session }: { session: EditorSession }): React.JSX.Element {
  return (
    <div className="grid gap-2" data-testid="editor-controls">
      {session.steps.length > 1 && !session.focused ? (
        <ul className="grid gap-1" aria-label={labels.steps.title} data-testid="editor-steps">
          {session.steps.map((step) => (
            <li key={step.key}>
              <Button
                variant={step.chosen ? 'secondary' : 'ghost'}
                size="sm"
                className="w-full justify-between"
                aria-pressed={step.chosen}
                data-testid="editor-step"
                data-manual={step.manual}
                onClick={() => session.choose(step.key)}
              >
                <span>{step.title}</span>
                <span className="font-normal text-muted-foreground">
                  {[step.detail, step.manual ? labels.steps.manual : labels.steps.auto]
                    .filter((part) => part !== null)
                    .join(' · ')}
                </span>
              </Button>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        {session.renderPanel()}
        {session.alwaysOn ? null : (
          <Button
            variant={session.active ? 'secondary' : 'outline'}
            size="sm"
            aria-pressed={session.active}
            data-testid="editor-open"
            onClick={session.active ? session.close : session.open}
          >
            <HandIcon />
            {labels.setByHand}
          </Button>
        )}
        {session.busy ? (
          <span
            className="flex items-center gap-1.5 text-sm text-muted-foreground"
            role="status"
            data-testid="editor-busy"
          >
            <LoaderCircleIcon className="size-4 animate-spin" aria-hidden="true" />
            {labels.saving}
          </span>
        ) : null}
      </div>
      {session.error === null ? null : <ErrorAlert message={session.error} />}
    </div>
  );
}
