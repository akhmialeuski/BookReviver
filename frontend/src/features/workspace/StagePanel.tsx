import type { Stage } from '@/api';
import { HistoryFrame } from '@/features/workspace/HistoryFrame';
import { PanelHeading } from '@/features/workspace/PanelHeading';
import { MESSAGES } from '@/shared/messages';
import { Badge } from '@/shared/ui/badge';

/**
 * The layout of the panel on the right of a stage screen, the one every stage fills and none lays out itself.
 *
 * The panel is drawn from slots, always in this order, and a slot that is empty is not drawn at all:
 *
 * 1. the header, which the layout draws from the stage: its name, its sentence and, for a stage that cannot be worked
 *    in yet, the word "Soon";
 * 2. `recipe`: the menu of the profile and the bar that saves the recipe, and on a stage without a step bar the picker of
 *    the recipe and the list of its steps with the button that adds one;
 * 3. `step`: the title of the open step, with no number, and the notes about the step itself, such as that it is off or
 *    stands out of its place;
 * 4. `settings`: every control that sets something, in one bordered frame the layout draws: the form of the open step,
 *    the values pages and parts of the pages have for its settings, the measurement of the book, and on the stages
 *    without steps the actions and fields of the file or of the selection;
 * 5. `page`: one section about the open page, file or selection, with its title, what stands for it (the page editor,
 *    the carry-over, a review box, a note) and its facts last, so the facts stand right above the history;
 * 6. `history`, the history of the open page, which every stage ends the scrolling area with, grey and shut with the
 *    reason when the stage keeps none;
 * 7. `footer`, under the scrolling area, for the buttons that start the work.
 *
 * Every grid item inside the panel has a zero minimum width, so no grid of a stage needs its own column template to stay
 * within the panel, however long a label in it is. The scrolling area is positioned, so what is placed absolutely inside
 * it, such as the hidden legend of a group of buttons, stays inside the area instead of hanging below the window and
 * making the whole book screen scroll.
 *
 * Sections are `gap-6` apart, what is inside a section is `gap-3` apart, and the blocks that make up the settings frame
 * are `gap-4` apart, on every stage. Every section heading is the `PanelHeading`.
 */

/** The title of the open step and the notes about the step itself. */
export interface StepSlot {
  /** The title of the step's processor, with no number. */
  title: string;
  /** The identifier of the step, which the section carries for whoever needs to tell steps apart. */
  stepId?: string;
  /** Notes about the step: that it is off, and where its place in the order is wrong, with the way to put it right. */
  children?: React.ReactNode;
}

/** The section about the open page, file or selection. */
export interface PageSlot {
  /** The heading: "This page · 12" on a stage of processors, the name of the file or the selection elsewhere. */
  title: string;
  /** A control that acts on what the title names, such as the one that clears a selection, beside the heading. */
  action?: React.ReactNode;
  /** What stands for the page: a note on its state, a box that asks for a review, the page editor, the carry-over. */
  children?: React.ReactNode;
  /** The list of facts about it, which is always the last element of the section, right above the history. */
  facts?: React.ReactNode;
}

/** Tell whether a slot has anything to draw. */
function filled(node: React.ReactNode): boolean {
  return node !== undefined && node !== null && node !== false;
}

export function StagePanel({
  stage,
  available,
  recipe,
  step,
  settings,
  page,
  history,
  footer,
}: {
  stage: Stage;
  /** Whether the stage can be worked in; false puts the word "Soon" beside the name. */
  available: boolean;
  /** The recipe: the profile menu, the save bar and, on a stage without a step bar, the picker and the list of steps. */
  recipe?: React.ReactNode;
  /** The open step: its title and the notes about it. */
  step?: StepSlot;
  /** Every setting, drawn in one bordered frame. */
  settings?: React.ReactNode;
  /** The one section about the open page, file or selection. */
  page?: PageSlot;
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
        {filled(recipe) ? (
          <section
            className="grid min-w-0 gap-3"
            aria-label={MESSAGES.processing.recipe.label}
            data-testid="panel-recipe"
          >
            <PanelHeading>{MESSAGES.processing.recipe.label}</PanelHeading>
            {recipe}
          </section>
        ) : null}
        {step === undefined ? null : (
          <section
            className="grid min-w-0 gap-3"
            aria-label={step.title}
            data-testid="panel-step"
            data-step-id={step.stepId}
          >
            <h3 className="text-base font-semibold" data-testid="step-panel-title">
              {step.title}
            </h3>
            {step.children}
          </section>
        )}
        {filled(settings) ? (
          <div className="grid min-w-0 gap-3 rounded-lg border p-3" data-testid="panel-settings">
            {settings}
          </div>
        ) : null}
        {page === undefined ? null : (
          <section className="grid min-w-0 gap-3" aria-label={page.title} data-testid="panel-page">
            <div className="flex min-h-6 items-center justify-between gap-2">
              <PanelHeading>{page.title}</PanelHeading>
              {page.action}
            </div>
            {page.children}
            {filled(page.facts) ? <div data-testid="panel-facts">{page.facts}</div> : null}
          </section>
        )}
        {history ?? <HistoryFrame count={null} reason={MESSAGES.workspace.history.noHistory} />}
      </div>
      {filled(footer) ? <footer className="border-t p-4">{footer}</footer> : null}
    </aside>
  );
}
