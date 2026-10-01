import type { PageSchema } from '@/api';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';

/**
 * The small picture of a page, shared by the page strip and the viewer's page panel.
 *
 * A page without images shows why in words: a placeholder waits for a scan and any other page is still being
 * prepared. The picture swaps in by itself when the manifest is read again after the server reports the page ready.
 */

export function PageThumbnail({
  page,
  alt,
  className,
}: {
  page: PageSchema;
  alt: string;
  className?: string;
}): React.JSX.Element {
  return (
    <div
      className={cn(
        'flex aspect-[3/4] items-center justify-center overflow-hidden rounded-md border bg-muted',
        page.included ? '' : 'opacity-50',
        className,
      )}
    >
      {page.images === null ? (
        <span className="px-2 text-center text-xs text-muted-foreground">
          {page.origin === 'placeholder' ? MESSAGES.pages.noImage : MESSAGES.pages.preparing}
        </span>
      ) : (
        <img
          src={page.images.thumbnail}
          alt={alt}
          loading="lazy"
          draggable={false}
          className="size-full object-contain"
        />
      )}
    </div>
  );
}
