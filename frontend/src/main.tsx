import { QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider } from '@tanstack/react-router';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { queryClient } from '@/app/query-client';
import { router } from '@/app/router';
import { configureApiClient } from '@/shared/http/client';
import './index.css';

/**
 * Entry point of the browser application: mounts the router inside the query client provider.
 */

configureApiClient(() => document.cookie);

const container = document.getElementById('root');
if (container === null) {
  throw new Error('The page has no #root element to mount the application in.');
}

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
