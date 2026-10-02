import { useQuery } from '@tanstack/react-query';
import { ArrowLeftIcon } from 'lucide-react';
import { useState } from 'react';
import { projectApiV1ProjectsProjectIdGetOptions } from '@/api/@tanstack/react-query.gen';
import { EmptyImport, ImportTips } from '@/features/import/EmptyImport';
import { FileList } from '@/features/import/FileList';
import { FilePanel } from '@/features/import/FilePanel';
import { importJobsOf } from '@/features/import/jobs';
import { useJobs, useScans, useSources } from '@/features/import/queries';
import { ScanGrid } from '@/features/import/ScanGrid';
import { ScanViewer } from '@/features/import/ScanViewer';
import { useManifest } from '@/features/pages/manifest';
import { ImportStatus } from '@/features/projects/upload/ImportStatus';
import type { StageSearch } from '@/features/workspace/params';
import { StageWorkspace } from '@/features/workspace/StageWorkspace';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Badge } from '@/shared/ui/badge';
import { Button } from '@/shared/ui/button';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The Import stage of a book: its files with the progress of the imports that run, the scans of the chosen file, and
 * the panel of that file.
 *
 * The chosen file and the open scan are the `source` and `scan` search params, so both are links that survive a
 * reload. A book without files and without an import under way shows the drop area instead of the list. This stage
 * has no strip of pages, since it works on files and scans, which the pages are cut from later.
 */

export function ImportScreen({
  projectId,
  search,
  onSearchChange,
}: {
  projectId: string;
  search: StageSearch;
  /** Merge changes into the search params of the route; an undefined value takes the param out. */
  onSearchChange: (changes: Partial<StageSearch>) => void;
}): React.JSX.Element {
  const project = useQuery(
    projectApiV1ProjectsProjectIdGetOptions({ path: { project_id: projectId } }),
  );
  const sources = useSources(projectId);
  const jobs = useJobs(projectId);
  const manifest = useManifest(projectId);
  const [dismissed, setDismissed] = useState<ReadonlySet<string>>(new Set());
  const [grid, setGrid] = useState<{ sourceId: string | undefined; page: number }>({
    sourceId: undefined,
    page: 1,
  });

  const files = sources.data ?? [];
  const selected = files.find((file) => file.id === search.source) ?? files[0];
  // A new file starts at the first page of its scans; deriving it here avoids an effect that resets the page
  const page = grid.sourceId === selected?.id ? grid.page : 1;
  const scans = useScans(projectId, selected?.id, page);
  const loaded = scans.data?.items ?? [];
  const opened = loaded.find((scan) => scan.id === search.scan);

  const failure = [project, sources, jobs, manifest].find((query) => query.isError);
  if (failure?.error) {
    return <ErrorAlert message={describeError(failure.error)} />;
  }
  if (
    project.data === undefined ||
    sources.data === undefined ||
    jobs.data === undefined ||
    manifest.data === undefined
  ) {
    return <p className="p-4 text-sm text-muted-foreground">{MESSAGES.common.loading}</p>;
  }

  const { active, report } = importJobsOf(jobs.data, dismissed);
  const reportBox =
    report === undefined ? null : (
      <ImportStatus
        job={report}
        onDismiss={() => setDismissed(new Set(dismissed).add(report.id))}
      />
    );

  let canvas: React.ReactNode;
  if (files.length === 0 && active.length === 0) {
    canvas = (
      <div className="h-full overflow-y-auto">
        <EmptyImport projectId={projectId} />
        {reportBox === null ? null : <div className="mx-auto max-w-3xl p-6 pt-0">{reportBox}</div>}
      </div>
    );
  } else if (opened !== undefined && selected !== undefined) {
    canvas = (
      <ScanViewer
        scans={loaded}
        scan={opened}
        total={selected.scan_count}
        onOpen={(scanId) => onSearchChange({ scan: scanId })}
      />
    );
  } else {
    canvas = (
      <div className="h-full overflow-y-auto">
        <div className="grid content-start gap-8 p-6">
          <FileList
            projectId={projectId}
            sources={files}
            imports={active}
            selectedId={selected?.id}
            onSelect={(sourceId) => onSearchChange({ source: sourceId, scan: undefined })}
          />
          {reportBox}
          {selected === undefined ? null : (
            <ScanGrid
              file={selected}
              scans={scans.data}
              error={scans.error}
              onPage={(next) => setGrid({ sourceId: selected.id, page: next })}
              onOpen={(scanId) => onSearchChange({ scan: scanId })}
            />
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex size-full flex-col" data-testid="stage-screen" data-stage="import">
      <div className="min-h-0 flex-1">
        <StageWorkspace
          strip={null}
          canvasHeader={
            opened === undefined || selected === undefined ? null : (
              <>
                <Button
                  variant="ghost"
                  size="sm"
                  data-testid="scan-back"
                  onClick={() => onSearchChange({ scan: undefined })}
                >
                  <ArrowLeftIcon />
                  {MESSAGES.import.viewer.back}
                </Button>
                <Badge variant="secondary">
                  {MESSAGES.import.viewer.chip(opened.number + 1, selected.file_name)}
                </Badge>
              </>
            )
          }
          canvas={canvas}
          panel={
            selected === undefined ? (
              <ImportTips />
            ) : (
              <FilePanel
                project={project.data}
                source={selected}
                scans={loaded}
                pages={manifest.data}
              />
            )
          }
        />
      </div>
    </div>
  );
}
