import type { Stage } from '@/api';
import { HistoryFrame } from '@/features/workspace/HistoryFrame';
import { MESSAGES } from '@/shared/messages';
import { Badge } from '@/shared/ui/badge';

/**
 * The frame of the panel on the right of a stage screen: the name of the stage, a sentence about what it does, a body
 * for the stage to fill and a footer for the buttons that start its work.
 *
 * The history of the open page is the last element of the scrolling area, above the footer, on every stage without
 * exception. A stage that keeps one passes its content as `history`, and a stage that keeps none gets the same frame,
 * grey and shut, with the reason. A stage with nothing to show yet leaves the body and the footer out, so the title, the
 * sentence and the history stand. A stage that cannot be worked in yet carries the word "Soon" beside its name.
 *
 * Every grid item inside the panel has a zero minimum width, so no grid of a stage needs its own column template to stay
 * within the panel, however long a label in it is.
 *
 * The scrolling area is positioned, so what is placed absolutely inside it, such as the hidden legend of a group of
 * buttons, stays inside the area instead of hanging below the window and making the whole book screen scroll.
 */

export function StagePanel({
  stage,
  available,
  children,
  history,
  footer,
}: {
  stage: Stage;
  /** Whether the stage can be worked in; false puts the word "Soon" beside the name. */
  available: boolean;
  /** The body of the panel. */
  children?: React.ReactNode;
  /** The history of the open page, drawn in a `HistoryFrame`, or nothing for a stage that keeps no history. */
  history?: React.ReactNode;
  /** The foot of the panel, for the buttons that start the work. */
  footer?: React.ReactNode;
}): React.JSX.Element {
  return (
    <aside
      // A grid with no column template sizes its one implicit column to the min-content of its widest item, and an
      // item whose min-width is auto cannot be narrower than that, so a long label would push the panel wider than its
      // column. Giving every grid item in the panel, the footer included, a zero minimum lets that column shrink.
      className="flex h-full flex-col [&_.grid>*]:min-w-0"
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
      <div
        className="relative flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto p-4"
        data-testid="stage-panel-scroll"
      >
        {children}
        {history ?? <HistoryFrame count={null} reason={MESSAGES.workspace.history.noHistory} />}
      </div>
      {footer === undefined ? null : <footer className="border-t p-4">{footer}</footer>}
    </aside>
  );
}
