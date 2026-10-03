import { useId } from 'react';
import type { BlankFill, PageSchema } from '@/api';
import { BLANK_FILLS } from '@/features/order/leaf';
import { commonOf } from '@/features/order/summary';
import { useFillBlankPages } from '@/features/pages/actions';
import { describePageError } from '@/features/pages/errors';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The block of the panel of selected pages that says what the image of blank pages is: their scan, a white leaf or a
 * leaf of the paper of the book.
 *
 * The block shows the choice the selected pages share, and none when they differ. A choice is written to every selected
 * page at once, and the button writes the chosen one to every blank page of the book. The leaf itself is drawn by the
 * server, so the choice is all the browser decides.
 */

export function BlankImageBlock({
  projectId,
  selected,
  blankPages,
}: {
  projectId: string;
  /** The selected pages, all of them blank pages cut from a scan. */
  selected: readonly PageSchema[];
  /** Every blank page of the book cut from a scan, which the button changes together. */
  blankPages: readonly PageSchema[];
}): React.JSX.Element {
  const text = MESSAGES.order.panel.leaf;
  const fill = useFillBlankPages(projectId);
  const name = useId();
  const current = commonOf(selected.map((page) => page.blank_fill));
  const change = (pages: readonly PageSchema[], chosen: BlankFill): void =>
    fill.mutate({
      path: { project_id: projectId },
      body: { page_ids: pages.map((page) => page.id), blank_fill: chosen },
    });

  return (
    <fieldset className="grid gap-3 rounded-md border p-3" data-testid="blank-leaf">
      <legend className="px-1 text-sm font-medium">{text.title}</legend>
      {BLANK_FILLS.map((option) => {
        const id = `${name}-${option}`;
        return (
          <div key={option} className="flex items-start gap-2">
            <input
              id={id}
              type="radio"
              name={name}
              className="mt-1 size-4 accent-primary"
              checked={current === option}
              aria-describedby={`${id}-hint`}
              onChange={() => change(selected, option)}
            />
            <div className="grid gap-0.5">
              <label htmlFor={id} className="text-sm">
                {text.options[option].label}
              </label>
              <p id={`${id}-hint`} className="text-xs text-muted-foreground">
                {text.options[option].hint}
              </p>
            </div>
          </div>
        );
      })}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={current === null}
          onClick={() => {
            if (current !== null) {
              change(blankPages, current);
            }
          }}
        >
          {text.applyAll(blankPages.length)}
        </Button>
        <p className="text-xs text-muted-foreground">{text.applyHint}</p>
      </div>
      {fill.isError ? <ErrorAlert message={describePageError(fill.error)} /> : null}
    </fieldset>
  );
}
