import { createFileRoute } from '@tanstack/react-router';
import { ProjectList } from '@/features/projects/ProjectList';

/**
 * The list of the books of the signed-in account. The page of the list is in the address, so a page can be linked
 * and the back button returns to it.
 */

export const Route = createFileRoute('/_authenticated/projects/')({
  validateSearch: (search: Record<string, unknown>): { page?: number } => {
    const page = Number(search.page);
    return Number.isInteger(page) && page > 1 ? { page } : {};
  },
  component: ProjectsPage,
});

function ProjectsPage(): React.JSX.Element {
  const { page = 1 } = Route.useSearch();
  const navigate = Route.useNavigate();
  return (
    <ProjectList
      page={page}
      onPageChange={(next, replace) =>
        void navigate({ search: { page: next > 1 ? next : undefined }, replace })
      }
    />
  );
}
