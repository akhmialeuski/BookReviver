import type { Stage } from '@/api';
import { MESSAGES } from '@/shared/messages';
import { Badge } from '@/shared/ui/badge';

/**
 * The frame of the panel on the right of a stage screen: the name of the stage, a sentence about what it does, a body
 * for the stage to fill and a footer for the buttons that start its work.
 *
 * A stage with nothing to show yet leaves the body and the footer out, so only the title and the sentence stand. A
 * stage that cannot be worked in yet carries the word "Soon" beside its name.
 */

export function StagePanel({
  stage,
  available,
  children,
  footer,
}: {
  stage: Stage;
  /** Whether the stage can be worked in; false puts the word "Soon" beside the name. */
  available: boolean;
  /** The body of the panel. */
  children?: React.ReactNode;
  /** The foot of the panel, for the buttons that start the work. */
  footer?: React.ReactNode;
}): React.JSX.Element {
  return (
    <aside
      className="flex h-full flex-col"
      aria-label={MESSAGES.stages.names[stage]}
      data-testid="stage-panel"
    >
      <header className="grid gap-1 border-b px-4 py-3">
        <div className="flex items-center gap-2">
          <h2 className="text-base font-semibold" data-testid="stage-title">
            {MESSAGES.stages.names[stage]}
          </h2>
          {available ? null : (
            <Badge variant="secondary">{MESSAGES.stages.status.unavailable}</Badge>
          )}
        </div>
        <p className="text-sm text-muted-foreground" data-testid="stage-summary">
          {MESSAGES.stages.summaries[stage]}
        </p>
      </header>
      {children === undefined ? null : (
        <div className="min-h-0 flex-1 overflow-y-auto p-4">{children}</div>
      )}
      {children === undefined ? <div className="flex-1" /> : null}
      {footer === undefined ? null : <footer className="border-t p-4">{footer}</footer>}
    </aside>
  );
}
