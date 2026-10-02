import {
  ArrowRightIcon,
  BookOpenIcon,
  CircleCheckIcon,
  FileTextIcon,
  MaximizeIcon,
} from 'lucide-react';
import { useState } from 'react';
import { DropZone } from '@/features/projects/upload/DropZone';
import { SkippedFiles } from '@/features/projects/upload/FileReview';
import type { PickedFile, SkippedFile } from '@/features/projects/upload/files';
import { EMPTY_SELECTION, selectionReducer } from '@/features/projects/upload/selection';
import { useUpload } from '@/features/projects/upload/useUpload';
import { StagePanel } from '@/features/workspace/StagePanel';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The Import stage of a book without files: a large drop area that starts the import as soon as files are picked or
 * dropped, the road of the files to the pages in three steps, and in the panel what is good to know before the first
 * upload.
 *
 * No list is shown to check first, since a book with no files has nothing to lose by an order the Order stage can fix
 * later. Files the pick leaves out are named only when nothing is left to send, because the stage turns to the list
 * of files at once otherwise.
 */

const STEPS = [
  { icon: FileTextIcon, ...MESSAGES.import.empty.steps.files },
  { icon: MaximizeIcon, ...MESSAGES.import.empty.steps.scans },
  { icon: BookOpenIcon, ...MESSAGES.import.empty.steps.pages },
] as const;

export function EmptyImport({ projectId }: { projectId: string }): React.JSX.Element {
  const [skipped, setSkipped] = useState<SkippedFile[]>([]);
  const { upload, send } = useUpload(projectId);

  const onPick = (picked: PickedFile[]): void => {
    const selection = selectionReducer(EMPTY_SELECTION, { type: 'add', picked });
    setSkipped(selection.skipped);
    if (selection.files.length > 0) {
      send(selection.files);
    }
  };

  return (
    <div
      className="mx-auto grid min-h-full max-w-3xl content-center gap-8 p-6"
      data-testid="import-empty"
    >
      <div className="grid gap-3">
        <DropZone onPick={onPick} className="gap-4 p-12" />
        <p className="text-center text-sm text-muted-foreground">{MESSAGES.import.empty.hint}</p>
        {upload.isPending ? (
          <p className="text-center text-sm text-muted-foreground">{MESSAGES.upload.submitting}</p>
        ) : null}
        {upload.isError ? <ErrorAlert message={describeError(upload.error)} /> : null}
        <SkippedFiles skipped={skipped} />
      </div>
      <ol className="flex items-start justify-center gap-4" aria-label={MESSAGES.import.empty.path}>
        {STEPS.map(({ icon: Icon, name, text }, index) => (
          <li key={name} className="flex items-start gap-4">
            {index === 0 ? null : (
              <ArrowRightIcon className="mt-4 size-4 text-muted-foreground" aria-hidden="true" />
            )}
            <div className="grid w-44 justify-items-center gap-1 text-center">
              <span className="flex size-10 items-center justify-center rounded-lg border bg-card">
                <Icon className="size-5" aria-hidden="true" />
              </span>
              <span className="text-sm font-medium">{name}</span>
              <span className="text-xs text-muted-foreground">{text}</span>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

/** The panel of a book without files, which answers what comes up before the first upload. */
export function ImportTips(): React.JSX.Element {
  return (
    <StagePanel stage="import" available>
      <section className="grid gap-3" data-testid="import-tips">
        <h3 className="text-xs font-medium tracking-wider text-muted-foreground uppercase">
          {MESSAGES.import.empty.tipsTitle}
        </h3>
        <ul className="grid gap-3 text-sm">
          {MESSAGES.import.empty.tips.map((tip) => (
            <li key={tip} className="flex items-start gap-2">
              <CircleCheckIcon className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              {tip}
            </li>
          ))}
        </ul>
      </section>
    </StagePanel>
  );
}
