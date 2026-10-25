import { useState } from 'react';
import type { AppliedProfileSchema, RecipeKind, RecipeProfileSchema } from '@/api';
import type { Processing } from '@/features/processing/useProcessing';
import { DeleteProfileDialog, RenameProfileDialog } from '@/features/profiles/ProfileDialogs';
import { fileNameOf, saveTextFile, writeProfileFile } from '@/features/profiles/profileFile';
import {
  useApplyProfile,
  useDuplicateProfile,
  useExportProfile,
} from '@/features/profiles/queries';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * One profile of the library of a book: its name, the steps it holds, and what can be done with it.
 *
 * The profile is applied to the recipe of one kind of page of the book, which the library lets the reader choose. It
 * can also be copied, saved to a file, renamed and deleted. What an action did is written under the card, with the steps
 * that had to be left out.
 */

const labels = MESSAGES.profiles.library;
const page = MESSAGES.profiles.page;

/** The book the library is opened from, whose profiles it lists and whose recipes take them. */
export interface LibraryBook {
  processing: Processing;
}

/** Write the steps of a profile in one line, with the steps that are off. */
function stepsLine(profile: RecipeProfileSchema, titles: ReadonlyMap<string, string>): string {
  if (profile.steps.length === 0) {
    return labels.steps.none;
  }
  return profile.steps
    .map((step) => {
      const title = titles.get(step.processor_key) ?? step.processor_key;
      return step.enabled ? title : labels.steps.titleOff(title);
    })
    .join(' · ');
}

/** What an action of a card did: a sentence, or the answer of an apply. */
type CardNotice = string | AppliedProfileSchema;

/** The button that applies a profile to a recipe of the book. It is a component of its own because it reads the state of the recipe. */
function BookActions({
  profile,
  book,
  kind,
  onApplied,
}: {
  profile: RecipeProfileSchema;
  book: LibraryBook;
  /** The kind of page whose recipe takes the steps of the profile. */
  kind: RecipeKind;
  onApplied: (notice: CardNotice) => void;
}): React.JSX.Element {
  const { processing } = book;
  const apply = useApplyProfile(processing.bookId);
  const dirty = processing.dirty && processing.stage === profile.stage;

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        title={dirty ? labels.applyDirty : labels.applyBookHint}
        disabled={dirty || apply.isPending}
        data-testid="profile-apply-book"
        onClick={() =>
          apply.mutate(
            {
              path: { book_id: processing.bookId, profile_id: profile.id },
              body: { kind },
            },
            {
              onSuccess: (applied) => {
                if (profile.stage === processing.stage) {
                  processing.chooseRecipe(applied.recipe.id);
                }
                onApplied(applied);
              },
            },
          )
        }
      >
        {apply.isPending
          ? labels.applying
          : labels.applyBook(MESSAGES.processing.recipe.kinds[kind])}
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
  kind = 'text',
}: {
  profile: RecipeProfileSchema;
  /** Titles of the installed processors by key, for the steps of the profile. */
  titles: ReadonlyMap<string, string>;
  /** The book the library is opened from. */
  book: LibraryBook;
  /** The kind of page whose recipe applying to the book changes, which the library lets the reader choose. */
  kind?: RecipeKind;
}): React.JSX.Element {
  const { bookId } = book.processing;
  const duplicate = useDuplicateProfile(bookId);
  const exporter = useExportProfile(bookId);
  const [notice, setNotice] = useState<CardNotice | null>(null);
  const error = duplicate.error ?? exporter.error;

  return (
    <li
      className="grid gap-2 rounded-lg border bg-card p-3 text-card-foreground"
      data-testid="profile-row"
      data-name={profile.name}
    >
      <h3 className="min-w-0 truncate font-medium" data-testid="profile-name">
        {profile.name}
      </h3>
      <p className="text-sm text-muted-foreground" data-testid="profile-steps">
        {stepsLine(profile, titles)}
      </p>
      <div className="flex flex-wrap gap-2">
        <BookActions profile={profile} book={book} kind={kind} onApplied={setNotice} />
        <Button
          variant="outline"
          size="sm"
          title={labels.duplicateHint}
          disabled={duplicate.isPending}
          data-testid="profile-duplicate"
          onClick={() =>
            duplicate.mutate(
              { path: { book_id: bookId, profile_id: profile.id } },
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
        <RenameProfileDialog bookId={bookId} profile={profile}>
          <Button variant="ghost" size="sm" data-testid="profile-rename">
            {page.rename}
          </Button>
        </RenameProfileDialog>
        <DeleteProfileDialog bookId={bookId} profile={profile}>
          <Button variant="ghost" size="sm" data-testid="profile-delete">
            {page.remove}
          </Button>
        </DeleteProfileDialog>
      </div>
      <Notice notice={notice} profileName={profile.name} />
      {error === null ? null : <ErrorAlert message={describeError(error)} />}
    </li>
  );
}

/** What the last action did: a sentence, or the answer of an apply, which names the steps that were left out. */
function Notice({
  notice,
  profileName,
}: {
  notice: CardNotice | null;
  profileName: string;
}): React.JSX.Element | null {
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
  return (
    <div className="grid gap-1 text-xs text-muted-foreground" data-testid="profile-notice">
      <p>{labels.appliedBook(MESSAGES.processing.recipe.kinds[notice.recipe.kind], profileName)}</p>
      {notice.missing_processors.length === 0 ? null : (
        <p className="text-status-attention" data-testid="profile-left-out">
          {MESSAGES.profiles.apply.leftOut(notice.missing_processors)}
        </p>
      )}
    </div>
  );
}
