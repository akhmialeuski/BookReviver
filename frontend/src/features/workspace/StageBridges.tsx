import type { Stage } from '@/api';
import { PageStrip } from '@/features/pages/PageStrip';

/**
 * A bridge: what the old page of the book held, mounted on the stage that owns it in the epic, until the task
 * "Order workspace" replaces it with its own layout.
 *
 * The Order stage shows the page strip with its page actions, as the page of the book had it, so the pages can still
 * be arranged while the stage screen is built. A stage that has a bridge takes the place of the strip and the canvas
 * of the workspace. The Import stage has no bridge any more, since it has a screen of its own.
 */

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
    case 'page-order':
      return <OrderBridge projectId={projectId} />;
    default:
      return null;
  }
}
