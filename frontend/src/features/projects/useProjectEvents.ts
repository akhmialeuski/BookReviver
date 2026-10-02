import { useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { projectEventsApiV1ProjectsProjectIdEventsGet } from '@/api';
import { applyProjectEvent, refreshProject } from '@/features/projects/events';
import { invalidateJobs } from '@/features/projects/queries';
import { keepListening } from '@/features/projects/reconnect';

/**
 * Keeps the open book up to date while its page is shown, by listening to the project's event stream.
 *
 * The stream is read with the generated server-sent events client, which sends the session cookie and gives the
 * name and data of every event. The connection is closed when the page is left. The generated client ends its stream
 * without a word when the server closes it, so reconnecting is done by `keepListening`, which opens the stream again
 * until the page is left and refreshes the book after every end, since the events of the gap are lost. The jobs are
 * read again after every connection too, since the end of a job before the server subscribed the stream has no event.
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
          // The jobs are read again once the server has subscribed this stream, so the end of a job that came between
          // the first read of the screen and the subscription is not lost for good, which would leave the screen
          // waiting for it. The rest of the book is not read again, which costs a long book a heavy second read
          fetch: async (input, init) => {
            const response = await fetch(input, init);
            if (response.ok) {
              void invalidateJobs(queryClient, projectId);
            }
            return response;
          },
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
