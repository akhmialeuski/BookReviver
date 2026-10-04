import { useState } from 'react';
import type { AppliedProfileSchema } from '@/api';
import type { Processing } from '@/features/processing/useProcessing';
import { ApplyProfileDialog } from '@/features/profiles/ApplyProfileDialog';
import { MESSAGES } from '@/shared/messages';

/**
 * The button of the recipe panel that applies a profile of the account to the open book. What it did is written under
 * the button, with the steps that had to be left out. Keeping the steps of the book in a profile is the profile menu.
 */

const labels = MESSAGES.profiles;

export function ProfileActions({ processing }: { processing: Processing }): React.JSX.Element {
  const [applied, setApplied] = useState<AppliedProfileSchema | null>(null);

  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap gap-2">
        <ApplyProfileDialog processing={processing} onApplied={setApplied} />
      </div>
      {applied === null ? null : (
        <div className="grid gap-1 text-xs text-muted-foreground" data-testid="profile-applied">
          <p>{labels.apply.applied(applied.recipe.name, applied.recipe.active)}</p>
          {applied.missing_processors.length === 0 ? null : (
            <p className="text-status-attention" data-testid="profile-left-out">
              {labels.apply.leftOut(applied.missing_processors)}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
