import { useState } from 'react';
import type { AppliedProfileSchema, LibraryProfileSchema } from '@/api';
import { useRunInFlight } from '@/features/processing/queries';
import { pageIdsFor, RunScope } from '@/features/processing/scope';
import type { Processing } from '@/features/processing/useProcessing';
import { DeleteProfileDialog, RenameProfileDialog } from '@/features/profiles/ProfileDialogs';
import { fileNameOf, saveTextFile, writeProfileFile } from '@/features/profiles/profileFile';
import {
  useApplyProfile,
  useDuplicateProfile,
  useExportProfile,
  useSetDefaultProfile,
  useUnsetDefaultProfile,
} from '@/features/profiles/queries';
import { useActiveJobs } from '@/features/workspace/queries';
import type { StripItem } from '@/features/workspace/strip';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Badge } from '@/shared/ui/badge';
import { Button } from '@/shared/ui/button';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * One profile of the library: its name, the books that use it, the steps it holds, and what can be done with it.
 *
 * Inside a book the profile is applied to the book, which makes it the active recipe, or to the selected pages, which
 * makes it a variant pinned to them and runs the stage on them, as giving any variant to pages does. The rest works
 * without a book: copying the profile, saving it to a file, choosing it as the default of its stage, renaming and
 * deleting it. What an action did is written under the card, with the steps that had to be left out.
 */

const labels = MESSAGES.profiles.library;
const page = MESSAGES.profiles.page;

/** The book the library is opened from, which is what makes applying a profile possible. */
export interface LibraryBook {
  processing: Processing;
  /** Every page of the book with where it stands in the stage, which the selection is read from. */
  items: readonly StripItem[];
  /** The pages selected in the grid. */
  selected: ReadonlySet<string>;
}

/** Write the steps of a profile in one line, with the pages a step is limited to and the steps that are off. */
function stepsLine(profile: LibraryProfileSchema, titles: ReadonlyMap<string, string>): string {
  if (profile.steps.length === 0) {
    return labels.steps.none;
  }
  const conditions = MESSAGES.processing.steps.condition.options;
  return profile.steps
    .map((step) => {
      const tags = [
        ...(step.applies_to === 'all' ? [] : [conditions[step.applies_to].toLowerCase()]),
        ...(step.enabled ? [] : [labels.steps.off]),
      ];
      const title = titles.get(step.processor_key) ?? step.processor_key;
      return tags.length === 0 ? title : `${title} (${tags.join(', ')})`;
    })
    .join(' · ');
}

/** What an action of a card did: a sentence, or the answer of an apply with the number of pages it was given. */
type CardNotice = string | { applied: AppliedProfileSchema; pages: number | null };

/**
 * The two buttons that apply a profile inside a book. They are a component of their own because they read the jobs of
 * the book, which a library opened from the account has none of.
 */
function BookActions({
  profile,
  book,
  onApplied,
}: {
  profile: LibraryProfileSchema;
  book: LibraryBook;
  onApplied: (notice: CardNotice) => void;
}): React.JSX.Element {
  const { processing } = book;
  const apply = useApplyProfile(processing.projectId, profile.stage);
  const activeJobs = useActiveJobs(processing.projectId);
  const runInFlight = useRunInFlight(processing.projectId);
  const selectedIds = pageIdsFor(RunScope.Selected, book.items, undefined, book.selected) ?? [];
  const busy = apply.isPending || (activeJobs.data?.length ?? 0) > 0 || runInFlight;
  const dirty = processing.dirty && processing.stage === profile.stage;

  const applyTo = (activate: boolean, pages: string[] | null): void => {
    apply.mutate(
      {
        path: { project_id: processing.projectId, profile_id: profile.id },
        body: pages === null ? { activate } : { activate, page_ids: pages },
      },
      {
        onSuccess: (applied) => {
          if (profile.stage === processing.stage) {
            processing.chooseRecipe(applied.recipe.id);
          }
          onApplied({ applied, pages: pages === null ? null : pages.length });
        },
      },
    );
  };

  let pagesBlock: string | undefined;
  if (dirty) {
    pagesBlock = labels.applyDirty;
  } else if (busy) {
    pagesBlock = labels.applyBusy;
  } else if (selectedIds.length === 0) {
    pagesBlock = labels.applyPagesNone;
  }

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        title={dirty ? labels.applyDirty : labels.applyBookHint}
        disabled={dirty || apply.isPending}
        data-testid="profile-apply-book"
        onClick={() => applyTo(true, null)}
      >
        {apply.isPending ? labels.applying : labels.applyBook}
      </Button>
      <Button
        variant="outline"
        size="sm"
        title={pagesBlock ?? labels.applyPagesHint}
        disabled={pagesBlock !== undefined}
        data-testid="profile-apply-pages"
        onClick={() => applyTo(false, selectedIds)}
      >
        {labels.applyPages}
      </Button>
      {apply.isError ? (
        <div className="basis-full">
          <ErrorAlert message={describeError(apply.error)} />
        </div>
      ) : null}
    </>
  );
}

export function ProfileCard({
  profile,
  titles,
  book,
}: {
  profile: LibraryProfileSchema;
  /** Titles of the installed processors by key, for the steps of the profile. */
  titles: ReadonlyMap<string, string>;
  /** The book the library is opened from, or nothing when it is opened from the account. */
  book?: LibraryBook;
}): React.JSX.Element {
  const duplicate = useDuplicateProfile();
  const exporter = useExportProfile();
  const choose = useSetDefaultProfile();
  const giveUp = useUnsetDefaultProfile();
  const [notice, setNotice] = useState<CardNotice | null>(null);
  const error = duplicate.error ?? exporter.error ?? choose.error ?? giveUp.error;

  return (
    <li
      className="grid gap-2 rounded-lg border bg-card p-3 text-card-foreground"
      data-testid="profile-row"
      data-name={profile.name}
      data-default={profile.is_default}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-2">
        <h3 className="min-w-0 truncate font-medium" data-testid="profile-name">
          {profile.name}
        </h3>
        <p className="text-xs text-muted-foreground">
          <span data-testid="profile-books">{labels.usedIn(profile.books)}</span>
          {profile.is_default ? (
            <>
              {' · '}
              <Badge
                variant="secondary"
                title={page.defaultBadge}
                data-testid="profile-default-badge"
              >
                {labels.defaultMark}
              </Badge>
            </>
          ) : null}
        </p>
      </div>
      <p className="text-sm text-muted-foreground" data-testid="profile-steps">
        {stepsLine(profile, titles)}
      </p>
      <div className="flex flex-wrap gap-2">
        {book === undefined ? null : (
          <BookActions profile={profile} book={book} onApplied={setNotice} />
        )}
        <Button
          variant="outline"
          size="sm"
          title={labels.duplicateHint}
          disabled={duplicate.isPending}
          data-testid="profile-duplicate"
          onClick={() =>
            duplicate.mutate(
              { path: { profile_id: profile.id } },
              { onSuccess: (copy) => setNotice(labels.duplicated(copy.name)) },
            )
          }
        >
          {labels.duplicate}
        </Button>
        <Button
          variant="outline"
          size="sm"
          title={labels.exportHint}
          disabled={exporter.isPending}
          data-testid="profile-export"
          onClick={() =>
            exporter.mutate(profile.id, {
              onSuccess: (file) => {
                saveTextFile(fileNameOf(profile.name), writeProfileFile(file));
                setNotice(labels.exported(profile.name));
              },
            })
          }
        >
          {labels.export}
        </Button>
      </div>
      <div className="flex flex-wrap gap-1">
        {profile.is_default ? (
          <Button
            variant="ghost"
            size="sm"
            title={page.unsetDefaultHint}
            disabled={giveUp.isPending}
            data-testid="profile-unset-default"
            onClick={() => giveUp.mutate({ path: { profile_id: profile.id } })}
          >
            {page.unsetDefault}
          </Button>
        ) : (
          <Button
            variant="ghost"
            size="sm"
            title={page.makeDefaultHint}
            disabled={choose.isPending}
            data-testid="profile-make-default"
            onClick={() => choose.mutate({ path: { profile_id: profile.id } })}
          >
            {page.makeDefault}
          </Button>
        )}
        <RenameProfileDialog profile={profile}>
          <Button variant="ghost" size="sm" data-testid="profile-rename">
            {page.rename}
          </Button>
        </RenameProfileDialog>
        <DeleteProfileDialog profile={profile}>
          <Button variant="ghost" size="sm" data-testid="profile-delete">
            {page.remove}
          </Button>
        </DeleteProfileDialog>
      </div>
      <Notice notice={notice} />
      {error === null ? null : <ErrorAlert message={describeError(error)} />}
    </li>
  );
}

/** What the last action did: a sentence, or the answer of an apply, which names the steps that were left out. */
function Notice({ notice }: { notice: CardNotice | null }): React.JSX.Element | null {
  if (notice === null) {
    return null;
  }
  if (typeof notice === 'string') {
    return (
      <p className="text-xs text-muted-foreground" data-testid="profile-notice">
        {notice}
      </p>
    );
  }
  const { applied, pages } = notice;
  return (
    <div className="grid gap-1 text-xs text-muted-foreground" data-testid="profile-notice">
      <p>
        {pages === null
          ? labels.appliedBook(applied.recipe.name)
          : labels.appliedPages(applied.recipe.name, pages)}
      </p>
      {applied.missing_processors.length === 0 ? null : (
        <p className="text-status-attention" data-testid="profile-left-out">
          {MESSAGES.profiles.apply.leftOut(applied.missing_processors)}
        </p>
      )}
    </div>
  );
}
