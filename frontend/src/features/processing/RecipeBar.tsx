import { useState } from 'react';
import type { StagePageSchema } from '@/api';
import { RecipePicker } from '@/features/processing/RecipePicker';
import { RecipeSaveBar } from '@/features/processing/RecipeSaveBar';
import type { Processing } from '@/features/processing/useProcessing';
import { ProfileLibraryPanel } from '@/features/profiles/ProfileLibraryPanel';
import { ProfileMenu } from '@/features/profiles/ProfileMenu';

/**
 * The bar that holds the profile and the recipe of a stage, which is one component for every stage and for both places that
 * show it: the top of the panel, above the open step, and the window of the gear.
 *
 * The first row is the button of the profile the recipe was made from, whose menu also opens the library of profiles, and the
 * choice of the recipe of the stage, which is left out while the stage has one recipe. A narrow amber row with Save and
 * Discard stands right under it while the draft differs from the saved recipe, so the bar is one row normally and two
 * with changes. Nothing is saved while a step is edited: the draft lives in the state of the screen, so a preview can use it,
 * and the recipe is written only by the button, which says first how many pages the save makes out of date.
 *
 * A stage whose recipe has not been read yet has no bar.
 */

export function RecipeBar({
  processing,
  rows,
}: {
  processing: Processing;
  /** The rows of the stage, which the number of pages a save makes out of date is counted from. */
  rows: readonly StagePageSchema[];
}): React.JSX.Element | null {
  const [libraryOpen, setLibraryOpen] = useState(false);
  if (processing.recipe === undefined) {
    return null;
  }

  return (
    <div className="grid gap-2" data-testid="recipe-bar">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <ProfileMenu processing={processing} onManage={() => setLibraryOpen(true)} />
        </div>
        {/* The choice of the recipe is as wide as its label, so the name of the profile takes the rest of the row */}
        <RecipePicker
          processing={processing}
          testId="recipe-bar-select"
          className="h-8 w-auto max-w-1/2 shrink-0 px-2"
        />
      </div>
      <RecipeSaveBar processing={processing} rows={rows} />
      <ProfileLibraryPanel open={libraryOpen} onOpenChange={setLibraryOpen} book={{ processing }} />
    </div>
  );
}
