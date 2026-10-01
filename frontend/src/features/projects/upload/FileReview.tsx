import { ArrowDownIcon, ArrowUpIcon, FolderIcon, XIcon } from 'lucide-react';
import { Direction, fileNameOf, groupByFolder, totalBytes } from '@/features/projects/upload/files';
import type { Selection, SelectionAction } from '@/features/projects/upload/selection';
import { formatBytes } from '@/shared/lib/format';
import { MESSAGES } from '@/shared/messages';
import { Badge } from '@/shared/ui/badge';
import { Button } from '@/shared/ui/button';

/**
 * The list a person checks before sending: every file in the order the book will have, with its kind, a button to
 * take it out and buttons to move it, and the same buttons for a whole folder. Files the upload leaves out, such as
 * the system files of a folder, are named below with the reason.
 */

export function FileReview({
  selection,
  dispatch,
}: {
  selection: Selection;
  dispatch: React.Dispatch<SelectionAction>;
}): React.JSX.Element {
  const { files, skipped } = selection;
  const groups = groupByFolder(files);
  let position = 0;

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-medium">
          {MESSAGES.upload.review.title(files.length, formatBytes(totalBytes(files)))}
        </p>
        <div className="flex gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => dispatch({ type: 'sort' })}
          >
            {MESSAGES.upload.review.sortByName}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => dispatch({ type: 'clear' })}
          >
            {MESSAGES.upload.review.clear}
          </Button>
        </div>
      </div>

      {files.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {skipped.length === 0
            ? MESSAGES.upload.review.empty
            : MESSAGES.upload.review.nothingToUpload}
        </p>
      ) : (
        <ol className="max-h-80 overflow-y-auto rounded-md border" data-testid="file-list">
          {groups.map((group, groupIndex) => {
            const folderName =
              group.folder === '' ? MESSAGES.upload.review.rootFolder : group.folder;
            return (
              // The first file names the run: a folder can be split into two runs, a path is unique
              <li key={group.files[0]?.path ?? group.folder}>
                <div className="sticky top-0 flex items-center gap-2 border-b bg-muted px-3 py-1.5 text-xs font-medium">
                  <FolderIcon className="size-4 shrink-0" aria-hidden="true" />
                  <span className="min-w-0 flex-1 break-all">{folderName}</span>
                  <span className="text-muted-foreground">{group.files.length}</span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    disabled={groupIndex === 0}
                    aria-label={MESSAGES.upload.review.moveFolderUp(folderName)}
                    onClick={() =>
                      dispatch({ type: 'move-folder', groupIndex, direction: Direction.Up })
                    }
                  >
                    <ArrowUpIcon />
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    disabled={groupIndex === groups.length - 1}
                    aria-label={MESSAGES.upload.review.moveFolderDown(folderName)}
                    onClick={() =>
                      dispatch({ type: 'move-folder', groupIndex, direction: Direction.Down })
                    }
                  >
                    <ArrowDownIcon />
                  </Button>
                </div>
                <ul className="divide-y">
                  {group.files.map((file) => {
                    const index = position;
                    position += 1;
                    const name = fileNameOf(file.path);
                    return (
                      <li
                        key={file.path}
                        className="flex items-center gap-2 px-3 py-1 text-sm [contain-intrinsic-size:auto_2.25rem] [content-visibility:auto]"
                      >
                        <span className="w-10 shrink-0 text-right text-xs text-muted-foreground tabular-nums">
                          {index + 1}
                        </span>
                        <span className="min-w-0 flex-1 break-all" data-testid="file-name">
                          {name}
                        </span>
                        <Badge variant="outline">{MESSAGES.upload.kinds[file.kind]}</Badge>
                        <span className="hidden w-16 shrink-0 text-right text-xs text-muted-foreground sm:inline">
                          {formatBytes(file.file.size)}
                        </span>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-sm"
                          disabled={index === 0}
                          aria-label={MESSAGES.upload.review.moveUp(name)}
                          onClick={() =>
                            dispatch({
                              type: 'move-file',
                              path: file.path,
                              direction: Direction.Up,
                            })
                          }
                        >
                          <ArrowUpIcon />
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-sm"
                          disabled={index === files.length - 1}
                          aria-label={MESSAGES.upload.review.moveDown(name)}
                          onClick={() =>
                            dispatch({
                              type: 'move-file',
                              path: file.path,
                              direction: Direction.Down,
                            })
                          }
                        >
                          <ArrowDownIcon />
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-sm"
                          aria-label={MESSAGES.upload.review.remove(name)}
                          onClick={() => dispatch({ type: 'remove', path: file.path })}
                        >
                          <XIcon />
                        </Button>
                      </li>
                    );
                  })}
                </ul>
              </li>
            );
          })}
        </ol>
      )}

      {skipped.length === 0 ? null : (
        <details className="rounded-md border px-3 py-2 text-sm" data-testid="skipped-files">
          <summary className="cursor-pointer font-medium">
            {MESSAGES.upload.skipped.title(skipped.length)}
          </summary>
          <p className="mt-2 text-xs text-muted-foreground">
            {MESSAGES.upload.skipped.description}
          </p>
          <ul className="mt-2 max-h-40 overflow-y-auto">
            {skipped.map((entry) => (
              <li
                key={`${entry.reason}:${entry.path}`}
                className="flex flex-wrap gap-x-3 py-0.5 text-xs"
              >
                <span className="break-all font-medium">{entry.path}</span>
                <span className="text-muted-foreground">
                  {MESSAGES.upload.skipped.reasons[entry.reason]}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
