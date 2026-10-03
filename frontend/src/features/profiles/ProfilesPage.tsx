import type { RecipeProfileSchema, Stage } from '@/api';
import { useProcessors } from '@/features/processing/queries';
import { DeleteProfileDialog, RenameProfileDialog } from '@/features/profiles/ProfileDialogs';
import {
  useProfiles,
  useSetDefaultProfile,
  useUnsetDefaultProfile,
} from '@/features/profiles/queries';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Badge } from '@/shared/ui/badge';
import { Button } from '@/shared/ui/button';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The recipe profiles of the signed-in account, by stage: each with the steps it holds, and the buttons that rename it,
 * make it the default of its stage or take that away, and delete it.
 *
 * The server lists the profiles in the order of the stages, so the groups come out in the order of the pipeline.
 */

const labels = MESSAGES.profiles.page;

/** Group the profiles by stage, keeping the order they are listed in. */
function byStage(profiles: readonly RecipeProfileSchema[]): [Stage, RecipeProfileSchema[]][] {
  const groups = new Map<Stage, RecipeProfileSchema[]>();
  for (const profile of profiles) {
    groups.set(profile.stage, [...(groups.get(profile.stage) ?? []), profile]);
  }
  return [...groups];
}

function ProfileRow({
  profile,
  titles,
}: {
  profile: RecipeProfileSchema;
  /** Titles of the installed processors by key, for the steps of the profile. */
  titles: ReadonlyMap<string, string>;
}): React.JSX.Element {
  const choose = useSetDefaultProfile();
  const giveUp = useUnsetDefaultProfile();
  const error = choose.error ?? giveUp.error;
  const stepNames = profile.steps.map((step) => {
    const title = titles.get(step.processor_key) ?? step.processor_key;
    return step.enabled ? title : labels.stepOff(title);
  });

  return (
    <li
      className="grid gap-2 rounded-lg border bg-card p-4 text-card-foreground"
      data-testid="profile-row"
      data-name={profile.name}
      data-default={profile.is_default}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <h3 className="truncate font-medium" data-testid="profile-name">
            {profile.name}
          </h3>
          {profile.is_default ? (
            <Badge variant="secondary" data-testid="profile-default-badge">
              {labels.defaultBadge}
            </Badge>
          ) : null}
        </div>
        <div className="flex flex-wrap gap-2">
          {profile.is_default ? (
            <Button
              variant="outline"
              size="sm"
              title={labels.unsetDefaultHint}
              disabled={giveUp.isPending}
              data-testid="profile-unset-default"
              onClick={() => giveUp.mutate({ path: { profile_id: profile.id } })}
            >
              {labels.unsetDefault}
            </Button>
          ) : (
            <Button
              variant="outline"
              size="sm"
              title={labels.makeDefaultHint}
              disabled={choose.isPending}
              data-testid="profile-make-default"
              onClick={() => choose.mutate({ path: { profile_id: profile.id } })}
            >
              {labels.makeDefault}
            </Button>
          )}
          <RenameProfileDialog profile={profile}>
            <Button variant="outline" size="sm" data-testid="profile-rename">
              {labels.rename}
            </Button>
          </RenameProfileDialog>
          <DeleteProfileDialog profile={profile}>
            <Button variant="outline" size="sm" data-testid="profile-delete">
              {labels.remove}
            </Button>
          </DeleteProfileDialog>
        </div>
      </div>
      <p className="text-sm text-muted-foreground" data-testid="profile-steps">
        {labels.steps(profile.steps.length)}: {stepNames.join(' → ')}
      </p>
      {error === null ? null : <ErrorAlert message={describeError(error)} />}
    </li>
  );
}

export function ProfilesPage(): React.JSX.Element {
  const profiles = useProfiles(undefined);
  const processors = useProcessors();
  const titles = new Map(
    (processors.data ?? []).map((processor) => [processor.key, processor.title]),
  );

  let body: React.JSX.Element;
  if (profiles.isError) {
    body = <ErrorAlert message={describeError(profiles.error)} />;
  } else if (profiles.data === undefined) {
    body = <p className="text-sm text-muted-foreground">{labels.loading}</p>;
  } else if (profiles.data.length === 0) {
    body = (
      <p className="text-sm text-muted-foreground" data-testid="profiles-empty">
        {labels.empty}
      </p>
    );
  } else {
    body = (
      <div className="grid gap-6">
        {byStage(profiles.data).map(([stage, group]) => (
          <section
            key={stage}
            className="grid gap-3"
            data-testid="profile-stage"
            data-stage={stage}
          >
            <h2 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
              {MESSAGES.stages.names[stage]}
            </h2>
            <ul className="grid gap-3">
              {group.map((profile) => (
                <ProfileRow key={profile.id} profile={profile} titles={titles} />
              ))}
            </ul>
          </section>
        ))}
      </div>
    );
  }

  return (
    <div className="grid gap-6">
      <div className="grid gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">{labels.title}</h1>
        <p className="text-sm text-muted-foreground">{labels.description}</p>
      </div>
      {body}
    </div>
  );
}
