import { createFileRoute } from '@tanstack/react-router';
import { BookList } from '@/features/books/BookList';
import { PageContainer } from '@/shared/ui/page-container';

/**
 * The list of the books of the signed-in account. The page of the list is in the address, so a page can be linked
 * and the back button returns to it.
 */

export const Route = createFileRoute('/_authenticated/books/')({
  validateSearch: (search: Record<string, unknown>): { page?: number } => {
    const page = Number(search.page);
    return Number.isInteger(page) && page > 1 ? { page } : {};
  },
  component: BooksPage,
});

function BooksPage(): React.JSX.Element {
  const { page = 1 } = Route.useSearch();
  const navigate = Route.useNavigate();
  return (
    <PageContainer>
      <BookList
        page={page}
        onPageChange={(next, replace) =>
          void navigate({ search: { page: next > 1 ? next : undefined }, replace })
        }
      />
    </PageContainer>
  );
}
