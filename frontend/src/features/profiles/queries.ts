import { type QueryClient, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { Stage } from '@/api';
import {
  applyProfileApiV1BooksBookIdRecipeProfilesProfileIdApplyPostMutation,
  createProfileApiV1BooksBookIdRecipeProfilesPostMutation,
  deleteProfileApiV1BooksBookIdRecipeProfilesProfileIdDeleteMutation,
  duplicateProfileApiV1BooksBookIdRecipeProfilesProfileIdDuplicatePostMutation,
  exportProfileApiV1BooksBookIdRecipeProfilesProfileIdExportGetOptions,
  importProfileApiV1BooksBookIdRecipeProfilesImportPostMutation,
  listProfilesApiV1BooksBookIdRecipeProfilesGetOptions,
  putProfileApiV1BooksBookIdRecipeProfilesProfileIdPutMutation,
  putRecipeProfileApiV1BooksBookIdStagesStageRecipesRecipeIdProfilePutMutation,
  renameProfileApiV1BooksBookIdRecipeProfilesProfileIdPatchMutation,
} from '@/api/@tanstack/react-query.gen';
import { invalidateAllRecipes, invalidateProfiles } from '@/features/books/queries';

/**
 * The queries and changes behind the recipe profiles of a book: the list of them, by stage or all, and the saving,
 * replacing, renaming, copying, exporting, importing, deleting, applying to a recipe of the book and linking a recipe to
 * one.
 *
 * Every change of a profile reads the lists of the book again. Applying a profile changes the recipes of one stage of
 * the book, so it reads the stage and the list again, and the jobs too, since applying it to pages queues a run.
 */

/** Profiles asked for per request; the route accepts at most this many. */
const LIST_SIZE = 100;

/**
 * Read the profiles of a book in the order of the stages, then oldest first.
 *
 * @param bookId The book whose profiles are wanted.
 * @param stage The stage whose profiles are wanted, or undefined for every stage.
 * @param enabled Whether to read them at all.
 */
export function useProfiles(bookId: string, stage: Stage | undefined, enabled = true) {
  return useQuery({
    ...listProfilesApiV1BooksBookIdRecipeProfilesGetOptions({
      path: { book_id: bookId },
      query: stage === undefined ? { size: LIST_SIZE } : { size: LIST_SIZE, stage },
    }),
    select: (page) => page.items,
    enabled,
  });
}

/** Read every list of the profiles of a book again. */
function refreshProfiles(queryClient: QueryClient, bookId: string): Promise<void> {
  return invalidateProfiles(queryClient, bookId);
}

/** Save the steps of a recipe as a profile of the book. */
export function useSaveProfile(bookId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...createProfileApiV1BooksBookIdRecipeProfilesPostMutation(),
    onSettled: () => refreshProfiles(queryClient, bookId),
  });
}

/** Replace the name, the steps and the order of a profile, which is how a book saves its changes to its profile. */
export function useReplaceProfile(bookId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...putProfileApiV1BooksBookIdRecipeProfilesProfileIdPutMutation(),
    onSettled: () => refreshProfiles(queryClient, bookId),
  });
}

/**
 * Record which profile a recipe of a book was made from. The steps of the recipe do not change, so no page goes stale,
 * but the recipes are read again, since they carry the link.
 */
export function useLinkProfile(bookId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...putRecipeProfileApiV1BooksBookIdStagesStageRecipesRecipeIdProfilePutMutation(),
    onSettled: () => invalidateAllRecipes(queryClient, bookId),
  });
}

/** Give a profile another name. */
export function useRenameProfile(bookId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...renameProfileApiV1BooksBookIdRecipeProfilesProfileIdPatchMutation(),
    onSettled: () => refreshProfiles(queryClient, bookId),
  });
}

/** Delete a profile, which changes no step of any recipe and only clears the links to it. */
export function useDeleteProfile(bookId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...deleteProfileApiV1BooksBookIdRecipeProfilesProfileIdDeleteMutation(),
    onSettled: () =>
      Promise.all([
        invalidateAllRecipes(queryClient, bookId),
        refreshProfiles(queryClient, bookId),
      ]),
  });
}

/**
 * Put the steps of a profile into the recipe of one kind of page of the book. The pages that recipe made go out of date,
 * and the profile is linked to the recipe.
 */
export function useApplyProfile(bookId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...applyProfileApiV1BooksBookIdRecipeProfilesProfileIdApplyPostMutation(),
    onSettled: () =>
      Promise.all([
        invalidateAllRecipes(queryClient, bookId),
        refreshProfiles(queryClient, bookId),
      ]),
  });
}

/** Save a copy of a profile, under its name with "(copy)" after it. */
export function useDuplicateProfile(bookId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...duplicateProfileApiV1BooksBookIdRecipeProfilesProfileIdDuplicatePostMutation(),
    onSettled: () => refreshProfiles(queryClient, bookId),
  });
}

/** Save the profile a file holds. The server checks it as it checks a saved profile, and names what is wrong with it. */
export function useImportProfile(bookId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...importProfileApiV1BooksBookIdRecipeProfilesImportPostMutation(),
    onSettled: () => refreshProfiles(queryClient, bookId),
  });
}

/** Read a profile as the file it is exchanged by. The file is read again every time, so it is never an old one. */
export function useExportProfile(bookId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (profileId: string) =>
      queryClient.fetchQuery({
        ...exportProfileApiV1BooksBookIdRecipeProfilesProfileIdExportGetOptions({
          path: { book_id: bookId, profile_id: profileId },
        }),
        staleTime: 0,
      }),
  });
}
