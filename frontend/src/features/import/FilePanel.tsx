import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { ArrowDownUpIcon, FileImageIcon, FileTextIcon, ListOrderedIcon } from 'lucide-react';
import type { PageSchema, ProjectSchema, ScanSchema, SourceSchema } from '@/api';
import {
  projectApiV1ProjectsProjectIdGetQueryKey,
  updateProjectApiV1ProjectsProjectIdPatchMutation,
} from '@/api/@tanstack/react-query.gen';
import { planSave, toFields } from '@/features/about/fields';
import { SuggestionBox } from '@/features/about/SuggestionBanners';
import { planSuggestion } from '@/features/about/suggestion';
import { formatRuns, pageRunsOf, resolutionOf } from '@/features/import/facts';
import { DeleteSourceDialog } from '@/features/projects/DeleteSourceDialog';
import { invalidateProjectList } from '@/features/projects/queries';
import { FactList } from '@/features/workspace/FactList';
import { PanelHeading } from '@/features/workspace/PanelHeading';
import { StagePanel } from '@/features/workspace/StagePanel';
import { describeError } from '@/shared/http/problem';
import { formatBytes, formatDateTime } from '@/shared/lib/format';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The panel of the Import stage for the chosen file, in the slots of the `StagePanel`: the settings frame holds what the
 * file says about the book where that differs from the description and the actions on its pages, the page section holds
 * the name of the file, what became of its pages and last the facts of the file, and the deletion of the file stands apart
 * at the foot.
 *
 * The import has already filled the empty fields of the description from the file, so the box of the file's own
 * suggestion appears only for what differs from the description, usually the title. Using it saves the description
 * through the same patch the About tab sends. The two actions on the pages of the file open the Order stage with the
 * file named in the address, and wait for pages to exist.
 */

export function FilePanel({
  project,
  source,
  scans,
  pages,
}: {
  project: ProjectSchema;
  source: SourceSchema;
  /** The scans of the file that are loaded, from which its resolution is read. */
  scans: readonly ScanSchema[];
  /** Every page of the book. */
  pages: readonly PageSchema[];
}): React.JSX.Element {
  const queryClient = useQueryClient();
  const use = useMutation({
    ...updateProjectApiV1ProjectsProjectIdPatchMutation(),
    onSuccess: (updated) => {
      queryClient.setQueryData(
        projectApiV1ProjectsProjectIdGetQueryKey({ path: { project_id: updated.id } }),
        updated,
      );
      void invalidateProjectList(queryClient);
    },
  });

  const labels = MESSAGES.import.panel;
  const Icon = source.kind === 'image' ? FileImageIcon : FileTextIcon;
  const runs = pageRunsOf(pages, source.id);
  const placed = runs.reduce((sum, run) => sum + run.last - run.first + 1, 0);
  const resolution = resolutionOf(scans);
  const fields = toFields(project);
  const plan = planSuggestion(fields, source.suggestion);
  const facts = [
    { label: labels.type, value: MESSAGES.import.files.kinds[source.file_type] },
    { label: labels.size, value: formatBytes(source.size_bytes) },
    { label: labels.scans, value: String(source.scan_count) },
    ...(resolution === null
      ? []
      : [
          {
            label: labels.resolution,
            value: labels.resolutionValue(resolution.min, resolution.max),
          },
        ]),
    { label: labels.imported, value: formatDateTime(source.imported_at) },
  ];

  const toOrder = (icon: React.ReactNode, label: string): React.JSX.Element =>
    placed === 0 ? (
      <Button variant="outline" className="justify-start" disabled>
        {icon}
        {label}
      </Button>
    ) : (
      <Button asChild variant="outline" className="justify-start">
        <Link
          to="/projects/$projectId/stages/$stage"
          params={{ projectId: project.id, stage: 'page-order' }}
          search={{ source: source.id }}
        >
          {icon}
          {label}
        </Link>
      </Button>
    );

  return (
    <StagePanel
      stage="import"
      available
      settings={
        <div className="grid gap-4">
          {plan.rows.length === 0 ? null : (
            <div className="grid gap-3">
              <PanelHeading>{labels.found}</PanelHeading>
              <SuggestionBox
                fileName={source.file_name}
                plan={plan}
                pending={use.isPending}
                onUse={() =>
                  use.mutate({
                    path: { project_id: project.id },
                    body: planSave(fields, plan.changes).patch,
                  })
                }
              />
              {use.isError ? <ErrorAlert message={describeError(use.error)} /> : null}
            </div>
          )}
          <div className="grid gap-3">
            <PanelHeading>{labels.pages}</PanelHeading>
            {toOrder(<ListOrderedIcon />, labels.showInOrder)}
            {toOrder(<ArrowDownUpIcon />, labels.moveElsewhere)}
          </div>
        </div>
      }
      page={{
        title: labels.file,
        children: (
          <div className="grid gap-1">
            <p className="flex items-center gap-2 font-semibold break-all">
              <Icon className="size-4 shrink-0" aria-hidden="true" />
              {source.file_name}
            </p>
            <p className="text-sm text-muted-foreground" data-testid="file-pages">
              {placed === 0
                ? labels.noPages
                : labels.becamePages(source.scan_count, placed, formatRuns(runs))}
            </p>
          </div>
        ),
        facts: <FactList facts={facts} />,
      }}
      footer={
        <div className="grid gap-2">
          <DeleteSourceDialog projectId={project.id} source={source} />
          <p className="text-xs text-muted-foreground">{labels.deleteNote}</p>
        </div>
      }
    />
  );
}
