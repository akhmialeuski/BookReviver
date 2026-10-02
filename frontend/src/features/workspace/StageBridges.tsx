import { useState } from 'react';
import type { Stage } from '@/api';
import { PageStrip } from '@/features/pages/PageStrip';
import { ScansPanel } from '@/features/projects/ScansPanel';
import { SourcesPanel } from '@/features/projects/SourcesPanel';
import { ImportStatus } from '@/features/projects/upload/ImportStatus';
import { UploadDialog } from '@/features/projects/upload/UploadDialog';

/**
 * A bridge: what the old page of the book held, mounted on the stages that own it in the epic, until the tasks
 * "Import workspace" and "Order workspace" replace it with their own layouts.
 *
 * The Import stage shows the upload with its import status, the files of the book and its scans, and the Order stage
 * shows the page strip with its page actions. The components are the ones the page of the book used, as they were, so
 * the book can still get pages and have them arranged while the stage screens are built. Each stage that has a bridge
 * takes the place of the strip and the canvas of the workspace.
 */

function ImportBridge({ projectId }: { projectId: string }): React.JSX.Element {
  const [sourceId, setSourceId] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);

  return (
    <div
      className="grid h-full content-start gap-6 overflow-y-auto p-4"
      data-testid="import-bridge"
    >
      <div className="flex justify-end">
        <UploadDialog projectId={projectId} onUploaded={(job) => setJobId(job.id)} />
      </div>
      {jobId === null ? null : (
        <ImportStatus projectId={projectId} jobId={jobId} onDismiss={() => setJobId(null)} />
      )}
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

function OrderBridge({ projectId }: { projectId: string }): React.JSX.Element {
  return (
    <div className="h-full overflow-y-auto p-4" data-testid="order-bridge">
      <PageStrip projectId={projectId} />
    </div>
  );
}

/**
 * Give the bridge of a stage.
 *
 * @param stage The stage open on the screen.
 * @param projectId The book.
 * @returns What takes the place of the strip and the canvas, or null for a stage that has the plain workspace.
 */
export function bridgeOf(stage: Stage, projectId: string): React.ReactNode | null {
  switch (stage) {
    case 'import':
      return <ImportBridge projectId={projectId} />;
    case 'page-order':
      return <OrderBridge projectId={projectId} />;
    default:
      return null;
  }
}
