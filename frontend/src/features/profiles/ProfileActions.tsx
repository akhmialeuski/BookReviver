import { useState } from 'react';
import type { AppliedProfileSchema } from '@/api';
import type { Processing } from '@/features/processing/useProcessing';
import { ApplyProfileDialog } from '@/features/profiles/ApplyProfileDialog';
import { SaveProfileDialog } from '@/features/profiles/SaveProfileDialog';
import { MESSAGES } from '@/shared/messages';

/**
 * The two buttons of the recipe panel that move a recipe between books through the account: save the steps on the screen
 * as a profile, and apply a profile to the open book. What the last of them did is written under the buttons, with the
 * steps that had to be left out.
 */

const labels = MESSAGES.profiles;

/** What the panel says about the last profile saved or applied. */
type Notice = { kind: 'saved'; name: string } | { kind: 'applied'; applied: AppliedProfileSchema };

export function ProfileActions({ processing }: { processing: Processing }): React.JSX.Element {
  const [notice, setNotice] = useState<Notice | null>(null);

  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap gap-2">
        <SaveProfileDialog
          processing={processing}
          onSaved={(name) => setNotice({ kind: 'saved', name })}
        />
        <ApplyProfileDialog
          processing={processing}
          onApplied={(applied) => setNotice({ kind: 'applied', applied })}
        />
      </div>
      {notice?.kind === 'saved' ? (
        <p className="text-xs text-muted-foreground" data-testid="profile-saved">
          {labels.save.saved(notice.name)}
        </p>
      ) : null}
      {notice?.kind === 'applied' ? (
        <div className="grid gap-1 text-xs text-muted-foreground" data-testid="profile-applied">
          <p>{labels.apply.applied(notice.applied.recipe.name, notice.applied.recipe.active)}</p>
          {notice.applied.missing_processors.length === 0 ? null : (
            <p className="text-status-attention" data-testid="profile-left-out">
              {labels.apply.leftOut(notice.applied.missing_processors)}
            </p>
          )}
        </div>
      ) : null}
    </div>
  );
}
