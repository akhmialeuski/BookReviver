import { PlusIcon } from 'lucide-react';
import { useState } from 'react';
import type { PageSchema, PaginationSectionSchema } from '@/api';
import { SectionDialog } from '@/features/order/SectionDialog';
import { draftOf, newSectionDraft } from '@/features/order/sectionDraft';
import { isSeries, type SectionSpan, sectionName } from '@/features/order/sections';
import { PanelHeading } from '@/features/workspace/PanelHeading';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';

/**
 * The pagination of the book on the Order stage, which stands in the settings frame of the panel with no border of its
 * own: its sections in book order, each with the bar of the colour its pages
 * carry on the grid, where it stands in the book, what it does to the numbers and the numbers it gives.
 *
 * A click on a section opens its form, and "Section" opens the form of a new one that starts at the first selected page.
 * The numbers themselves are written by the server, so the panel reads the sections and the pages and shows what is
 * there.
 */

/** What the section dialog is open for: a section that exists, or a new one that starts at a page. */
type SectionForm = { section: PaginationSectionSchema } | { firstId: string };

/** Where a section stands: the pages it covers, or for a series the kinds of page it takes across the book. */
function where(span: SectionSpan): string {
  return isSeries(span.section)
    ? span.section.kinds.map((kind) => MESSAGES.pages.kinds[kind]).join(', ')
    : MESSAGES.order.sections.range(span.firstPosition, span.lastPosition);
}

/** What a section does to its pages in a few words: its sequence, and whether it counts, prints and in which style. */
function summary(section: PaginationSectionSchema): string {
  const text = MESSAGES.order.sections;
  const parts: string[] = isSeries(section) ? [text.ownSequence] : [];
  if (section.display === 'not-counted') {
    parts.push(text.displays['not-counted'].label);
  } else if (section.display === 'counted') {
    parts.push(text.displays.counted.label);
  } else {
    parts.push(text.from(text.styleNames[section.style], section.start));
  }
  return parts.join(', ');
}

export function PaginationPanel({
  projectId,
  pages,
  spans,
  selectedId,
}: {
  projectId: string;
  /** The pages of the book in book order. */
  pages: readonly PageSchema[];
  /** The sections of the book with the pages they number, in book order. */
  spans: readonly SectionSpan[];
  /** The first selected page, where a new section starts, or undefined when no page is selected. */
  selectedId: string | undefined;
}): React.JSX.Element {
  const text = MESSAGES.order.sections;
  const [form, setForm] = useState<SectionForm | null>(null);
  const startsAt = pages.find((page) => page.id === selectedId)?.id ?? pages[0]?.id;

  return (
    <section className="grid gap-3" aria-label={text.title} data-testid="pagination-panel">
      <header className="flex items-center justify-between gap-2">
        <PanelHeading>{text.title}</PanelHeading>
        <Button
          variant="outline"
          size="sm"
          aria-label={text.addLabel}
          disabled={startsAt === undefined}
          onClick={() => startsAt !== undefined && setForm({ firstId: startsAt })}
          data-testid="section-add"
        >
          <PlusIcon />
          {text.add}
        </Button>
      </header>
      {spans.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="sections-empty">
          {text.empty}
        </p>
      ) : (
        <ul className="grid gap-1.5" aria-label={text.list}>
          {spans.map((span) => (
            <li key={span.section.id}>
              <button
                type="button"
                className={cn(
                  'grid w-full cursor-pointer gap-0.5 rounded-md border border-l-4 bg-background px-3 py-1.5 text-left text-sm hover:bg-accent/50',
                  span.tone.edge,
                )}
                data-testid="section-row"
                data-section-id={span.section.id}
                onClick={() => setForm({ section: span.section })}
              >
                <span className="flex items-baseline justify-between gap-2">
                  <span className="min-w-0 truncate">
                    <span className="font-medium" data-testid="section-name">
                      {sectionName(span)}
                    </span>{' '}
                    <span className="text-xs text-muted-foreground">{where(span)}</span>
                  </span>
                  <span className="shrink-0 text-xs" data-testid="section-numbers">
                    {span.labels === null
                      ? text.noNumbers
                      : text.numbers(span.labels.first, span.labels.last)}
                  </span>
                </span>
                <span className="text-xs text-muted-foreground">{summary(span.section)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <p className="text-xs text-muted-foreground">{text.hint}</p>
      {form === null ? null : (
        <SectionDialog
          key={'section' in form ? form.section.id : 'new'}
          projectId={projectId}
          pages={pages}
          section={'section' in form ? form.section : undefined}
          initial={'section' in form ? draftOf(form.section) : newSectionDraft(form.firstId)}
          onClose={() => setForm(null)}
        />
      )}
    </section>
  );
}
