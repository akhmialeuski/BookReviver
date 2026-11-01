import { defineConfig } from '@hey-api/openapi-ts';

/**
 * Configuration of `@hey-api/openapi-ts`, the generator of the API client.
 *
 * The client is built from the committed OpenAPI schema, never from a running server, so the same schema always
 * gives the same files. Run it with `npm run generate`; the files are committed and never edited.
 */

export default defineConfig({
  input: '../docs/openapi.json',
  output: { path: 'src/api' },
  plugins: [
    '@hey-api/typescript',
    '@hey-api/client-fetch',
    '@hey-api/sdk',
    '@tanstack/react-query',
  ],
});
