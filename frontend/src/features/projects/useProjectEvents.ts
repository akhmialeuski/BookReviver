import { useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { projectEventsApiV1ProjectsProjectIdEventsGet } from '@/api';
import { applyProjectEvent, refreshProject } from '@/features/projects/events';
import { keepListening } from '@/features/projects/reconnect';

/**
 * Keeps the open book up to date while its page is shown, by listening to the project's event stream.
 *
 * The stream is read with the generated server-sent events client, which sends the session cookie and gives the
 * name and data of every event. The connection is closed when the page is left. The generated client ends its stream
 * without a word when the server closes it, so reconnecting is done by `keepListening`, which opens the stream again
 * until the page is left and refreshes the book after every end, since the events of the gap are lost.
 */

// The generated client's own retry is off, so that one loop decides when to reconnect
const GENERATED_CLIENT_ATTEMPTS = 1;

export function useProjectEvents(projectId: string): void {
  const queryClient = useQueryClient();

  useEffect(() => {
    const controller = new AbortController();
    void keepListening({
      signal: controller.signal,
      catchUp: () => void refreshProject(queryClient, projectId),
      open: async (onActivity) => {
        const { stream } = await projectEventsApiV1ProjectsProjectIdEventsGet({
          path: { project_id: projectId },
          signal: controller.signal,
          sseMaxRetryAttempts: GENERATED_CLIENT_ATTEMPTS,
          onSseEvent: (event) => {
            onActivity();
            applyProjectEvent(queryClient, projectId, event);
          },
        });
        return stream;
      },
    });

    return () => controller.abort();
  }, [projectId, queryClient]);
}
