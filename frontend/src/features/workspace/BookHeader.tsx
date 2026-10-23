import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { BookOpenIcon, ChevronRightIcon, KeyboardIcon } from 'lucide-react';
import { bookApiV1BooksBookIdGetOptions } from '@/api/@tanstack/react-query.gen';
import { AccountMenu } from '@/features/auth/AppHeader';
import { ActivityChip } from '@/features/workspace/ActivityChip';
import { MESSAGES } from '@/shared/messages';
import { Badge } from '@/shared/ui/badge';
import { Button } from '@/shared/ui/button';

/**
 * The header of every screen of a book: the way back to the library, the title and year of the book, the activity of
 * its jobs, the reading mode, the shortcuts and the account menu.
 */

export function BookHeader({
  bookId,
  onShortcuts,
}: {
  bookId: string;
  onShortcuts: () => void;
}): React.JSX.Element {
  const book = useQuery(bookApiV1BooksBookIdGetOptions({ path: { book_id: bookId } }));
  const details = book.data?.details;

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b px-4">
      <Link to="/books" className="flex items-center gap-2 font-semibold tracking-tight">
        <BookOpenIcon className="size-5" aria-hidden="true" />
        {MESSAGES.app.name}
      </Link>
      <ChevronRightIcon
        className="hidden size-4 text-muted-foreground md:block"
        aria-hidden="true"
      />
      <Link
        to="/books"
        className="hidden text-sm text-muted-foreground hover:text-foreground md:block"
      >
        {MESSAGES.workspace.header.library}
      </Link>
      <ChevronRightIcon
        className="hidden size-4 text-muted-foreground md:block"
        aria-hidden="true"
      />
      <h1 className="truncate text-sm font-semibold" data-testid="book-title">
        {details === undefined ? '' : details.title || MESSAGES.books.untitled}
      </h1>
      {details?.publication_year ? (
        <Badge variant="secondary">{details.publication_year}</Badge>
      ) : null}
      <div className="ml-auto flex items-center gap-2">
        <ActivityChip bookId={bookId} />
        <Button asChild variant="outline" size="sm">
          <Link to="/books/$bookId/viewer" params={{ bookId }}>
            <BookOpenIcon />
            {MESSAGES.workspace.header.read}
          </Link>
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={MESSAGES.shortcuts.open}
          title={MESSAGES.shortcuts.open}
          onClick={onShortcuts}
        >
          <KeyboardIcon />
        </Button>
        <AccountMenu />
      </div>
    </header>
  );
}
