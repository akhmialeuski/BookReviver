import type { RecipeProfileSchema } from '@/api';
import { step } from '@/features/processing/fixtures';

/**
 * Profiles of an account as the tests of the profile screens build them, and the page of the list that holds them.
 */

/** A profile of the geometry stage with a switched-off step after a step that is on. */
export function profile(
  id: string,
  overrides: Partial<RecipeProfileSchema> = {},
): RecipeProfileSchema {
  return {
    id,
    stage: 'geometry',
    name: 'Photographed book',
    steps: [
      step('geometry.deskew', { params: { max_angle: 9 } }),
      step('geometry.crop', { enabled: false }),
    ],
    order: 'usual',
    is_default: false,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    ...overrides,
  };
}

/** The page of the list of profiles the server answers with. */
export function profilePage(items: readonly RecipeProfileSchema[]): {
  data: { items: RecipeProfileSchema[]; total: number; page: number; size: number; pages: number };
} {
  return { data: { items: [...items], total: items.length, page: 1, size: 100, pages: 1 } };
}
