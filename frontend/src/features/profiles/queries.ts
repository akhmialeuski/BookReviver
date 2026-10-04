import { type QueryClient, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { Stage } from '@/api';
import {
  applyProfileApiV1ProjectsProjectIdRecipeProfilesProfileIdApplyPostMutation,
  createProfileApiV1RecipeProfilesPostMutation,
  deleteDefaultProfileApiV1RecipeProfilesProfileIdDefaultDeleteMutation,
  deleteProfileApiV1RecipeProfilesProfileIdDeleteMutation,
  listProfilesApiV1RecipeProfilesGetOptions,
  listProfilesApiV1RecipeProfilesGetQueryKey,
  putDefaultProfileApiV1RecipeProfilesProfileIdDefaultPutMutation,
  putProfileApiV1RecipeProfilesProfileIdPutMutation,
  putRecipeProfileApiV1ProjectsProjectIdStagesStageVariantsRecipeIdProfilePutMutation,
  renameProfileApiV1RecipeProfilesProfileIdPatchMutation,
} from '@/api/@tanstack/react-query.gen';
import { refreshStage } from '@/features/processing/queries';

/**
 * The queries and changes behind the recipe profiles of the account: the list of them, by stage or all, and the saving,
 * replacing, renaming, choosing as the default, deleting, applying to a book and linking a recipe to one.
 *
 * Every change of a profile reads the lists again, since a new default takes the mark off the one before it. Applying a
 * profile changes the recipes of one stage of one book, not the profiles, so it marks that stage stale.
 */

/** Profiles asked for per request; the route accepts at most this many. */
const LIST_SIZE = 100;

/**
 * Read the profiles of the account in the order of the stages, then oldest first.
 *
 * @param stage The stage whose profiles are wanted, or undefined for every stage.
 * @param enabled Whether to read them at all.
 */
export function useProfiles(stage: Stage | undefined, enabled = true) {
  return useQuery({
    ...listProfilesApiV1RecipeProfilesGetOptions({
      query: stage === undefined ? { size: LIST_SIZE } : { size: LIST_SIZE, stage },
    }),
    select: (page) => page.items,
    enabled,
  });
}

/** Read every list of profiles again. */
function refreshProfiles(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: listProfilesApiV1RecipeProfilesGetQueryKey() });
}

/** Save the steps of a recipe as a profile of the account. */
export function useSaveProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    ...createProfileApiV1RecipeProfilesPostMutation(),
    onSettled: () => refreshProfiles(queryClient),
  });
}

/** Replace the name, the steps and the order of a profile, which is how a book saves its changes to its profile. */
export function useReplaceProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    ...putProfileApiV1RecipeProfilesProfileIdPutMutation(),
    onSettled: () => refreshProfiles(queryClient),
  });
}

/**
 * Record which profile a recipe of a book was made from. The steps of the recipe do not change, so no page goes stale,
 * but the recipes are read again, since they carry the link.
 */
export function useLinkProfile(projectId: string, stage: Stage) {
  const queryClient = useQueryClient();
  return useMutation({
    ...putRecipeProfileApiV1ProjectsProjectIdStagesStageVariantsRecipeIdProfilePutMutation(),
    onSettled: () => refreshStage(queryClient, projectId, stage),
  });
}

/** Give a profile another name. */
export function useRenameProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    ...renameProfileApiV1RecipeProfilesProfileIdPatchMutation(),
    onSettled: () => refreshProfiles(queryClient),
  });
}

/** Delete a profile, which changes no recipe of any book. */
export function useDeleteProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    ...deleteProfileApiV1RecipeProfilesProfileIdDeleteMutation(),
    onSettled: () => refreshProfiles(queryClient),
  });
}

/** Make a profile the default of its stage, which takes the mark off the one that was. */
export function useSetDefaultProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    ...putDefaultProfileApiV1RecipeProfilesProfileIdDefaultPutMutation(),
    onSettled: () => refreshProfiles(queryClient),
  });
}

/** Stop a profile being the default of its stage. */
export function useUnsetDefaultProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    ...deleteDefaultProfileApiV1RecipeProfilesProfileIdDefaultDeleteMutation(),
    onSettled: () => refreshProfiles(queryClient),
  });
}

/** Add the steps of a profile to a book as a variant of the stage, which may become the active recipe. */
export function useApplyProfile(projectId: string, stage: Stage) {
  const queryClient = useQueryClient();
  return useMutation({
    ...applyProfileApiV1ProjectsProjectIdRecipeProfilesProfileIdApplyPostMutation(),
    onSettled: () => refreshStage(queryClient, projectId, stage),
  });
}
