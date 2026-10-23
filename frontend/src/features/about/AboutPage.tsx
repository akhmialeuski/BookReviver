import { useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { DownloadIcon, EraserIcon, XIcon } from 'lucide-react';
import { useState } from 'react';
import type { BookSchema } from '@/api';
import { bookApiV1BooksBookIdGetOptions } from '@/api/@tanstack/react-query.gen';
import { DescriptionForm } from '@/features/about/DescriptionForm';
import { SaveStatus } from '@/features/about/SaveStatus';
import { SuggestionBanners } from '@/features/about/SuggestionBanners';
import { SECTIONS, type Section, sectionId } from '@/features/about/sections';
import { useAutosave } from '@/features/about/useAutosave';
import { archiveUrl } from '@/features/books/archive';
import { DeleteBookDialog } from '@/features/books/DeleteBookDialog';
import { ClearOldResults } from '@/features/processing/ClearOldResults';
import { describeError } from '@/shared/http/problem';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The About tab of a book: the sections of its description on the left, the forms on the right, and the actions on the
 * whole book last on the left, apart from the rest: the clearing of its old results, its download and its deletion.
 *
 * The forms save by themselves, and the line at the bottom says where the saving stands.
 */

function AboutForm({ book }: { book: BookSchema }): React.JSX.Element {
  const navigate = useNavigate();
  const autosave = useAutosave(book);
  const [current, setCurrent] = useState<Section>(SECTIONS[0] ?? 'title');

  const open = (section: Section): void => {
    setCurrent(section);
    document.getElementById(sectionId(section))?.scrollIntoView({ behavior: 'smooth' });
  };

  return (
    <div className="grid md:grid-cols-[16rem_1fr]">
      <nav
        aria-label={MESSAGES.about.navigation}
        className="grid content-start gap-1 border-b p-4 md:min-h-[calc(100vh-8rem)] md:border-r md:border-b-0"
      >
        {SECTIONS.map((section) => (
          <Button
            key={section}
            type="button"
            variant="ghost"
            aria-current={current === section ? 'true' : undefined}
            className={cn('justify-start', current === section ? 'bg-accent' : '')}
            onClick={() => open(section)}
          >
            {MESSAGES.about.sections[section]}
          </Button>
        ))}
        <div className="my-2 border-t" />
        <ClearOldResults bookId={book.id}>
          <Button type="button" variant="ghost" className="justify-start">
            <EraserIcon />
            {MESSAGES.about.clearResults.open}
          </Button>
        </ClearOldResults>
        <Button asChild variant="ghost" className="justify-start">
          <a href={archiveUrl(book.id)} download>
            <DownloadIcon />
            {MESSAGES.about.download}
          </a>
        </Button>
        <DeleteBookDialog book={book} onDeleted={() => void navigate({ to: '/books' })}>
          <Button
            type="button"
            variant="ghost"
            className="justify-start text-destructive hover:text-destructive"
          >
            <XIcon />
            {MESSAGES.about.delete}
          </Button>
        </DeleteBookDialog>
      </nav>
      <div className="grid max-w-5xl content-start gap-6 px-6 pt-6">
        <SuggestionBanners bookId={book.id} fields={autosave.fields} onEdit={autosave.edit} />
        <DescriptionForm
          fields={autosave.fields}
          problems={autosave.problems}
          onEdit={autosave.edit}
        />
        <SaveStatus
          state={autosave.state}
          savedAt={autosave.savedAt}
          error={autosave.error}
          onRetry={autosave.retry}
        />
      </div>
    </div>
  );
}

export function AboutPage({ bookId }: { bookId: string }): React.JSX.Element {
  const book = useQuery(bookApiV1BooksBookIdGetOptions({ path: { book_id: bookId } }));
  if (book.isError) {
    return <ErrorAlert message={describeError(book.error)} />;
  }
  if (book.data === undefined) {
    return <p className="p-6 text-sm text-muted-foreground">{MESSAGES.common.loading}</p>;
  }
  return <AboutForm key={book.data.id} book={book.data} />;
}
