import { useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { projectEventsApiV1ProjectsProjectIdEventsGet } from '@/api';
import { applyProjectEvent, refreshProject } from '@/features/projects/events';

/**
 * Keeps the open book up to date while its page is shown, by listening to the project's event stream.
 *
 * The stream is read with the generated server-sent events client, which sends the session cookie and gives the
 * name and data of every event. The connection is closed when the page is left. A dropped connection is retried
 * with a growing delay by the client, and events missed meanwhile are made up for by refreshing everything once.
 */

// Retries before the stream is given up; the job panel still polls, so a finished import is never missed
const MAX_RETRIES = 10;

export function useProjectEvents(projectId: string): void {
  const queryClient = useQueryClient();

  useEffect(() => {
    const controller = new AbortController();
    const catchUp = (): void => {
      if (!controller.signal.aborted) {
        void refreshProject(queryClient, projectId);
      }
    };

    const listen = async (): Promise<void> => {
      try {
        const { stream } = await projectEventsApiV1ProjectsProjectIdEventsGet({
          path: { project_id: projectId },
          signal: controller.signal,
          sseMaxRetryAttempts: MAX_RETRIES,
          onSseEvent: (event) => applyProjectEvent(queryClient, projectId, event),
          onSseError: catchUp,
        });
        // The events reach onSseEvent while the stream is read, so reading it is the whole job
        for await (const _event of stream) {
          // intentionally empty
        }
      } catch {
        catchUp();
      }
    };
    void listen();

    return () => controller.abort();
  }, [projectId, queryClient]);
}
