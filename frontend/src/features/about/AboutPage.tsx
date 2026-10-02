import { useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { XIcon } from 'lucide-react';
import { useState } from 'react';
import type { ProjectSchema } from '@/api';
import { projectApiV1ProjectsProjectIdGetOptions } from '@/api/@tanstack/react-query.gen';
import { CoverPicker } from '@/features/about/CoverPicker';
import { DescriptionForm } from '@/features/about/DescriptionForm';
import { SaveStatus } from '@/features/about/SaveStatus';
import { SuggestionBanners } from '@/features/about/SuggestionBanners';
import { SECTIONS, type Section, sectionId } from '@/features/about/sections';
import { useAutosave } from '@/features/about/useAutosave';
import { DeleteProjectDialog } from '@/features/projects/DeleteProjectDialog';
import { describeError } from '@/shared/http/problem';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The About tab of a book: the sections of its description on the left, the forms on the right, the choice of the
 * cover above them, and the deletion of the book last on the left, apart from the rest.
 *
 * The forms save by themselves, and the line at the bottom says where the saving stands.
 */

function AboutForm({ project }: { project: ProjectSchema }): React.JSX.Element {
  const navigate = useNavigate();
  const autosave = useAutosave(project);
  const [current, setCurrent] = useState<Section>(SECTIONS[0] ?? 'title');

  const open = (section: Section): void => {
    setCurrent(section);
    document.getElementById(sectionId(section))?.scrollIntoView({ behavior: 'smooth' });
  };

  return (
    <div className="grid md:grid-cols-[16rem_1fr]">
      <nav
        aria-label={MESSAGES.about.navigation}
        className="grid content-start gap-1 border-b p-4 md:min-h-[calc(100vh-8rem)] md:border-r md:border-b-0"
      >
        {SECTIONS.map((section) => (
          <Button
            key={section}
            type="button"
            variant="ghost"
            aria-current={current === section ? 'true' : undefined}
            className={cn('justify-start', current === section ? 'bg-accent' : '')}
            onClick={() => open(section)}
          >
            {MESSAGES.about.sections[section]}
          </Button>
        ))}
        <div className="my-2 border-t" />
        <DeleteProjectDialog project={project} onDeleted={() => void navigate({ to: '/projects' })}>
          <Button
            type="button"
            variant="ghost"
            className="justify-start text-destructive hover:text-destructive"
          >
            <XIcon />
            {MESSAGES.about.delete}
          </Button>
        </DeleteProjectDialog>
      </nav>
      <div className="grid max-w-5xl content-start gap-6 px-6 pt-6">
        <CoverPicker
          projectId={project.id}
          coverPageId={autosave.fields.cover_page_id}
          onChoose={(pageId) => autosave.edit({ cover_page_id: pageId }, { immediate: true })}
        />
        <SuggestionBanners projectId={project.id} fields={autosave.fields} onEdit={autosave.edit} />
        <DescriptionForm
          fields={autosave.fields}
          problems={autosave.problems}
          onEdit={autosave.edit}
        />
        <SaveStatus
          state={autosave.state}
          savedAt={autosave.savedAt}
          error={autosave.error}
          onRetry={autosave.retry}
        />
      </div>
    </div>
  );
}

export function AboutPage({ projectId }: { projectId: string }): React.JSX.Element {
  const project = useQuery(
    projectApiV1ProjectsProjectIdGetOptions({ path: { project_id: projectId } }),
  );
  if (project.isError) {
    return <ErrorAlert message={describeError(project.error)} />;
  }
  if (project.data === undefined) {
    return <p className="p-6 text-sm text-muted-foreground">{MESSAGES.common.loading}</p>;
  }
  return <AboutForm key={project.data.id} project={project.data} />;
}
