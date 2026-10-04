import { useRef, useState } from 'react';
import type { LibraryProfileSchema, Stage } from '@/api';
import { useProcessors } from '@/features/processing/queries';
import { type LibraryBook, ProfileCard } from '@/features/profiles/ProfileCard';
import { type FileProblem, readProfileFile } from '@/features/profiles/profileFile';
import { useImportProfile, useProfiles } from '@/features/profiles/queries';
import { SaveProfileDialog } from '@/features/profiles/SaveProfileDialog';
import { STAGES } from '@/features/stages/stages';
import { describeError } from '@/shared/http/problem';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The library of the profiles of the account: a tab for each stage that has profiles and one for all of them, a card
 * for each profile, and under them the two ways to add one, from the steps of the open book and from a file.
 *
 * Opened from a book, it starts on the stage of the book and its cards apply profiles to the book and to the selected
 * pages. Opened from the account it starts on all stages and the cards leave out what needs a book. A file is read here
 * and sent as it is, and the server's answer, which names what is wrong with it, is shown under the list.
 */

const labels = MESSAGES.profiles.library;
const ALL = 'all';

/** The tab of a stage, or the one that shows every stage. */
type Tab = Stage | typeof ALL;

/** The stages that have a profile, and the stage of the book, in the order of the pipeline. */
function stagesOf(profiles: readonly LibraryProfileSchema[], current: Stage | undefined): Stage[] {
  const present = new Set<Stage>(profiles.map((profile) => profile.stage));
  if (current !== undefined) {
    present.add(current);
  }
  return STAGES.map((entry) => entry.stage).filter((stage) => present.has(stage));
}

export function ProfileLibrary({ book }: { book?: LibraryBook }): React.JSX.Element {
  const profiles = useProfiles(undefined);
  const processors = useProcessors();
  const importer = useImportProfile();
  const [tab, setTab] = useState<Tab>(book?.processing.stage ?? ALL);
  const [saveOpen, setSaveOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [problem, setProblem] = useState<FileProblem | null>(null);
  const picker = useRef<HTMLInputElement | null>(null);
  const titles = new Map(
    (processors.data ?? []).map((processor) => [processor.key, processor.title]),
  );
  const all = profiles.data ?? [];
  const stages = stagesOf(all, book?.processing.stage);
  const tabs: Tab[] = [...stages, ALL];
  const shown = tab === ALL ? all : all.filter((profile) => profile.stage === tab);

  /** Read the chosen file and send it, which makes a profile or says what is wrong with the file. */
  const importFile = async (file: File): Promise<void> => {
    setNotice(null);
    setProblem(null);
    importer.reset();
    const read = readProfileFile(await file.text());
    if (!read.ok) {
      setProblem(read.problem);
      return;
    }
    importer.mutate(
      { body: read.file },
      { onSuccess: (profile) => setNotice(labels.imported(profile.name)) },
    );
  };

  let body: React.JSX.Element;
  if (profiles.isError) {
    body = <ErrorAlert message={describeError(profiles.error)} />;
  } else if (profiles.data === undefined) {
    body = <p className="text-sm text-muted-foreground">{MESSAGES.profiles.page.loading}</p>;
  } else if (shown.length === 0) {
    body = (
      <p className="text-sm text-muted-foreground" data-testid="profiles-empty">
        {tab === ALL ? labels.emptyAll : labels.empty(MESSAGES.stages.names[tab])}
      </p>
    );
  } else if (tab === ALL) {
    body = (
      <div className="grid gap-4">
        {stagesOf(shown, undefined).map((stage) => (
          <section
            key={stage}
            className="grid gap-2"
            data-testid="profile-stage"
            data-stage={stage}
          >
            <h2 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
              {MESSAGES.stages.names[stage]}
            </h2>
            <ul className="grid gap-2">
              {shown
                .filter((profile) => profile.stage === stage)
                .map((profile) => (
                  <ProfileCard key={profile.id} profile={profile} titles={titles} book={book} />
                ))}
            </ul>
          </section>
        ))}
      </div>
    );
  } else {
    body = (
      <ul className="grid gap-2">
        {shown.map((profile) => (
          <ProfileCard key={profile.id} profile={profile} titles={titles} book={book} />
        ))}
      </ul>
    );
  }

  return (
    <div className="grid gap-3" data-testid="profile-library">
      <div role="tablist" aria-label={labels.stages} className="flex flex-wrap gap-1">
        {tabs.map((entry) => (
          <button
            key={entry}
            type="button"
            role="tab"
            aria-selected={tab === entry}
            data-testid="profile-tab"
            data-tab={entry}
            className={cn(
              'rounded-full border px-3 py-0.5 text-xs',
              tab === entry ? 'bg-accent font-medium' : 'text-muted-foreground hover:bg-accent/50',
            )}
            onClick={() => setTab(entry)}
          >
            {entry === ALL ? labels.allStages : MESSAGES.stages.names[entry]}
          </button>
        ))}
      </div>
      {body}
      <div className="flex flex-wrap gap-2 border-t pt-3">
        {book === undefined ? null : (
          <Button
            variant="outline"
            size="sm"
            title={labels.fromBookHint}
            disabled={book.processing.recipe === undefined}
            data-testid="profile-from-book"
            onClick={() => setSaveOpen(true)}
          >
            {labels.fromBook}
          </Button>
        )}
        <Button
          variant="outline"
          size="sm"
          title={labels.importHint}
          disabled={importer.isPending}
          data-testid="profile-import"
          onClick={() => picker.current?.click()}
        >
          {importer.isPending ? labels.importing : labels.import}
        </Button>
        <input
          ref={picker}
          type="file"
          accept="application/json,.json"
          className="sr-only"
          aria-label={labels.fileLabel}
          data-testid="profile-import-file"
          onChange={(event) => {
            const chosen = event.target.files?.[0];
            // The same file can be chosen again after the problem in it was fixed
            event.target.value = '';
            if (chosen !== undefined) {
              void importFile(chosen);
            }
          }}
        />
      </div>
      {notice === null ? null : (
        <p className="text-xs text-muted-foreground" data-testid="profile-library-notice">
          {notice}
        </p>
      )}
      {problem === null ? null : (
        <div data-testid="profile-import-problem">
          <ErrorAlert message={labels.fileProblems[problem]} />
        </div>
      )}
      {importer.isError ? (
        <div data-testid="profile-import-error">
          <ErrorAlert message={describeError(importer.error)} />
        </div>
      ) : null}
      {book === undefined ? null : (
        <SaveProfileDialog
          processing={book.processing}
          open={saveOpen}
          onOpenChange={setSaveOpen}
          onSaved={(name) => setNotice(MESSAGES.profiles.save.saved(name))}
        />
      )}
    </div>
  );
}
