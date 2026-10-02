import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { BookOpenIcon, ChevronRightIcon, KeyboardIcon } from 'lucide-react';
import { projectApiV1ProjectsProjectIdGetOptions } from '@/api/@tanstack/react-query.gen';
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
  projectId,
  onShortcuts,
}: {
  projectId: string;
  onShortcuts: () => void;
}): React.JSX.Element {
  const project = useQuery(
    projectApiV1ProjectsProjectIdGetOptions({ path: { project_id: projectId } }),
  );
  const details = project.data?.details;

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b px-4">
      <Link to="/projects" className="flex items-center gap-2 font-semibold tracking-tight">
        <BookOpenIcon className="size-5" aria-hidden="true" />
        {MESSAGES.app.name}
      </Link>
      <ChevronRightIcon
        className="hidden size-4 text-muted-foreground md:block"
        aria-hidden="true"
      />
      <Link
        to="/projects"
        className="hidden text-sm text-muted-foreground hover:text-foreground md:block"
      >
        {MESSAGES.workspace.header.library}
      </Link>
      <ChevronRightIcon
        className="hidden size-4 text-muted-foreground md:block"
        aria-hidden="true"
      />
      <h1 className="truncate text-sm font-semibold" data-testid="book-title">
        {details === undefined ? '' : details.title || MESSAGES.projects.untitled}
      </h1>
      {details?.publication_year ? (
        <Badge variant="secondary">{details.publication_year}</Badge>
      ) : null}
      <div className="ml-auto flex items-center gap-2">
        <ActivityChip projectId={projectId} />
        <Button asChild variant="outline" size="sm">
          <Link to="/projects/$projectId/viewer" params={{ projectId }}>
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
