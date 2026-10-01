import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { ArrowLeftIcon } from 'lucide-react';
import { useState } from 'react';
import { projectApiV1ProjectsProjectIdGetOptions } from '@/api/@tanstack/react-query.gen';
import { BookDetails } from '@/features/projects/BookDetails';
import { ScansPanel } from '@/features/projects/ScansPanel';
import { SourcesPanel } from '@/features/projects/SourcesPanel';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Badge } from '@/shared/ui/badge';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The page of one book: its title and counts, the description, the files it was made from and its scans.
 */

export function ProjectPage({ projectId }: { projectId: string }): React.JSX.Element {
  const [sourceId, setSourceId] = useState<string | null>(null);
  const project = useQuery(
    projectApiV1ProjectsProjectIdGetOptions({ path: { project_id: projectId } }),
  );

  if (project.isError) {
    return <ErrorAlert message={describeError(project.error)} />;
  }
  if (project.data === undefined) {
    return <p className="text-sm text-muted-foreground">{MESSAGES.common.loading}</p>;
  }
  const { details } = project.data;

  return (
    <div className="grid gap-6">
      <Link
        to="/projects"
        className="inline-flex w-fit items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeftIcon className="size-4" />
        {MESSAGES.projects.back}
      </Link>
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="grid gap-2">
          <h1 className="text-2xl font-semibold tracking-tight">
            {details.title || MESSAGES.projects.untitled}
          </h1>
          <div className="flex flex-wrap gap-2">
            <Badge variant="secondary">
              {MESSAGES.projects.counts.pages(project.data.page_count)}
            </Badge>
            <Badge variant="secondary">
              {MESSAGES.projects.counts.sources(project.data.source_count)}
            </Badge>
            <Badge variant="secondary">
              {MESSAGES.projects.counts.scans(project.data.scan_count)}
            </Badge>
          </div>
        </div>
      </div>
      <BookDetails details={details} />
      <SourcesPanel
        projectId={projectId}
        selectedSourceId={sourceId}
        onSelectSource={setSourceId}
      />
      <ScansPanel
        projectId={projectId}
        sourceId={sourceId}
        onClearSource={() => setSourceId(null)}
      />
    </div>
  );
}
