import { ChevronLeftIcon, ChevronRightIcon } from 'lucide-react';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';

/**
 * Previous and next buttons with the position, for a collection the server pages with `page` and `pages`.
 */

export function Pager({
  page,
  pages,
  onPageChange,
}: {
  page: number;
  pages: number;
  onPageChange: (page: number) => void;
}): React.JSX.Element | null {
  if (pages <= 1) {
    return null;
  }
  return (
    <nav className="flex items-center justify-center gap-3" aria-label="Pages">
      <Button
        variant="outline"
        size="sm"
        disabled={page <= 1}
        onClick={() => onPageChange(page - 1)}
      >
        <ChevronLeftIcon />
        {MESSAGES.common.previous}
      </Button>
      <span className="text-sm text-muted-foreground">{MESSAGES.common.pageOf(page, pages)}</span>
      <Button
        variant="outline"
        size="sm"
        disabled={page >= pages}
        onClick={() => onPageChange(page + 1)}
      >
        {MESSAGES.common.next}
        <ChevronRightIcon />
      </Button>
    </nav>
  );
}
