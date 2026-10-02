import { useQuery } from '@tanstack/react-query';
import { LightbulbIcon } from 'lucide-react';
import { listSourcesApiV1ProjectsProjectIdSourcesGetOptions } from '@/api/@tanstack/react-query.gen';
import type { EditableFields } from '@/features/about/fields';
import { planSuggestion, suggestionSummary } from '@/features/about/suggestion';
import type { EditOptions } from '@/features/about/useAutosave';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';

/**
 * What the files of the book suggest for its description, one box per file that suggests something new.
 *
 * The import has already filled the empty fields, so a box appears only for a value that differs from the
 * description or an entry the lists lack. Using the suggestion changes the description and the box goes away.
 */

/** The most files the server lists in one request. */
const SOURCES_PAGE_SIZE = 100;

export function SuggestionBanners({
  projectId,
  fields,
  onEdit,
}: {
  projectId: string;
  fields: EditableFields;
  onEdit: (changes: Partial<EditableFields>, options?: EditOptions) => void;
}): React.JSX.Element | null {
  const sources = useQuery(
    listSourcesApiV1ProjectsProjectIdSourcesGetOptions({
      path: { project_id: projectId },
      query: { size: SOURCES_PAGE_SIZE },
    }),
  );
  const suggestions = (sources.data?.items ?? [])
    .map((source) => ({ source, plan: planSuggestion(fields, source.suggestion) }))
    .filter(({ plan }) => plan.rows.length > 0);

  if (suggestions.length === 0) {
    return null;
  }
  return (
    <ul className="grid gap-3">
      {suggestions.map(({ source, plan }) => (
        <li
          key={source.id}
          data-testid="suggestion"
          className="grid gap-3 rounded-xl border bg-muted/40 p-4 text-sm"
        >
          <p className="flex items-start gap-2">
            <LightbulbIcon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
            <span>
              {MESSAGES.about.suggestion.found(
                source.file_name,
                suggestionSummary(plan.rows, MESSAGES.about.suggestion.fields),
              )}
            </span>
          </p>
          <dl className="grid gap-x-4 gap-y-1 sm:grid-cols-[max-content_1fr]">
            {plan.rows.map((row) => (
              <div key={row.field} className="contents">
                <dt className="text-muted-foreground">
                  {MESSAGES.about.suggestion.rows[row.field]}
                </dt>
                <dd className="break-words">{row.text}</dd>
              </div>
            ))}
          </dl>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="w-fit"
            onClick={() => onEdit(plan.changes, { immediate: true })}
          >
            {MESSAGES.about.suggestion.use}
          </Button>
        </li>
      ))}
    </ul>
  );
}
