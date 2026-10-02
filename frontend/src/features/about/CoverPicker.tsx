import { useState } from 'react';
import { coverCandidates, coverIdOf } from '@/features/about/cover';
import { useManifest } from '@/features/pages/manifest';
import { PageThumbnail } from '@/features/pages/PageThumbnail';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';

/**
 * The choice of the page the library shows as the cover of the book.
 *
 * The pages that look like a cover are offered first, as the pages of a hundred-page book are too many to look
 * through, and every page is offered on request. Choosing a page is saved at once.
 */

export function CoverPicker({
  projectId,
  coverPageId,
  onChoose,
}: {
  projectId: string;
  /** The chosen cover, or null when none is chosen and the library shows the first page. */
  coverPageId: string | null;
  onChoose: (pageId: string) => void;
}): React.JSX.Element {
  const [showAll, setShowAll] = useState(false);
  const pages = useManifest(projectId).data ?? [];
  const chosen = coverIdOf(pages, coverPageId);
  const candidates = coverCandidates(pages, coverPageId, showAll);
  const messages = MESSAGES.about.cover;

  return (
    <section aria-labelledby="about-cover-title" className="grid gap-2">
      <h2 id="about-cover-title" className="text-sm font-medium">
        {messages.title}
      </h2>
      {pages.length === 0 ? (
        <p className="text-sm text-muted-foreground">{messages.empty}</p>
      ) : (
        <>
          <ul
            className={cn('flex flex-wrap gap-3 p-1', showAll ? 'max-h-80 overflow-y-auto' : '')}
            data-testid="cover-candidates"
          >
            {candidates.map(({ page, number }) => {
              const caption = messages.page(number, MESSAGES.pages.kinds[page.kind]);
              return (
                <li key={page.id}>
                  <button
                    type="button"
                    aria-pressed={page.id === chosen}
                    aria-label={messages.choose(caption)}
                    onClick={() => onChoose(page.id)}
                    className={cn(
                      'grid w-24 gap-1 rounded-md text-center text-xs text-muted-foreground outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50',
                      page.id === chosen ? 'ring-2 ring-primary' : '',
                    )}
                  >
                    <PageThumbnail page={page} alt="" />
                    <span>{caption}</span>
                  </button>
                </li>
              );
            })}
          </ul>
          {pages.length > candidates.length || showAll ? (
            <Button
              type="button"
              variant="link"
              size="sm"
              className="w-fit px-0"
              onClick={() => setShowAll(!showAll)}
            >
              {showAll ? messages.showFewer : messages.showAll}
            </Button>
          ) : null}
        </>
      )}
    </section>
  );
}
