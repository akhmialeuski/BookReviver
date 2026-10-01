import { Link } from '@tanstack/react-router';
import type { ProjectSchema } from '@/api';
import { DeleteProjectDialog } from '@/features/projects/DeleteProjectDialog';
import { formatDate } from '@/shared/lib/format';
import { MESSAGES } from '@/shared/messages';
import { Badge } from '@/shared/ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/shared/ui/card';

/**
 * One book of the list: its title and author, the number of pages, sources and scans, and when it last changed.
 */

export function ProjectCard({ project }: { project: ProjectSchema }): React.JSX.Element {
  const { details } = project;
  const byline = [details.primary_author, details.publication_year]
    .filter((part) => part !== '')
    .join(', ');
  return (
    <Card className="gap-4">
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <CardTitle className="text-lg leading-snug">
            <Link
              to="/projects/$projectId"
              params={{ projectId: project.id }}
              className="underline-offset-4 hover:underline"
            >
              {details.title || MESSAGES.projects.untitled}
            </Link>
          </CardTitle>
          <DeleteProjectDialog project={project} />
        </div>
        {byline === '' ? null : <CardDescription>{byline}</CardDescription>}
      </CardHeader>
      <CardContent className="grid gap-3">
        <div className="flex flex-wrap gap-2">
          <Badge variant="secondary">{MESSAGES.projects.counts.pages(project.page_count)}</Badge>
          <Badge variant="secondary">
            {MESSAGES.projects.counts.sources(project.source_count)}
          </Badge>
          <Badge variant="secondary">{MESSAGES.projects.counts.scans(project.scan_count)}</Badge>
        </div>
        <p className="text-xs text-muted-foreground">
          {MESSAGES.projects.updated(formatDate(project.updated_at))}
        </p>
      </CardContent>
    </Card>
  );
}
