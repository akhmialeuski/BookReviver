import type { BookDetailsSchema } from '@/api';
import { detailRows } from '@/features/projects/details';
import { MESSAGES } from '@/shared/messages';
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/ui/card';

/**
 * The description of the book as a list of the fields that have a value.
 */

export function BookDetails({ details }: { details: BookDetailsSchema }): React.JSX.Element {
  const rows = detailRows(details);
  return (
    <Card>
      <CardHeader>
        <CardTitle>{MESSAGES.book.details}</CardTitle>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">{MESSAGES.book.noDetails}</p>
        ) : (
          <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[max-content_1fr]">
            {rows.map((row) => (
              <div key={row.label} className="contents">
                <dt className="text-muted-foreground">{row.label}</dt>
                <dd className="whitespace-pre-line">{row.value}</dd>
              </div>
            ))}
          </dl>
        )}
      </CardContent>
    </Card>
  );
}
