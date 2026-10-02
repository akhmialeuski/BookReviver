import { FilesIcon, FolderOpenIcon, UploadCloudIcon } from 'lucide-react';
import { useRef, useState } from 'react';
import { collectDroppedFiles, filesOfInput } from '@/features/projects/upload/entries';
import type { PickedFile } from '@/features/projects/upload/files';
import { describeError } from '@/shared/http/problem';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * Where files are chosen: a folder or single files by button, or dropped on the zone. A dropped folder is read to
 * its last file before the files are handed on, which can take a moment for a book of thousands of pages.
 */

export function DropZone({
  onPick,
  className,
}: {
  onPick: (picked: PickedFile[]) => void;
  /** Classes for the drop target, such as the padding of a zone that fills a screen. */
  className?: string;
}): React.JSX.Element {
  const folderInput = useRef<HTMLInputElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [reading, setReading] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const onInputChange = (event: React.ChangeEvent<HTMLInputElement>): void => {
    onPick(filesOfInput(event.target.files));
    // Cleared so that choosing the same folder again, after removing files, still fires a change
    event.target.value = '';
  };

  const onDrop = async (event: React.DragEvent<HTMLDivElement>): Promise<void> => {
    event.preventDefault();
    setDragging(false);
    setFailure(null);
    setReading(true);
    try {
      onPick(await collectDroppedFiles(event.dataTransfer.items));
    } catch (error) {
      setFailure(describeError(error));
    } finally {
      setReading(false);
    }
  };

  return (
    <div className="grid gap-3">
      {/* biome-ignore lint/a11y/noStaticElementInteractions: a drop target has no keyboard role, the buttons inside do the same job */}
      <div
        data-testid="drop-zone"
        className={cn(
          'flex flex-col items-center gap-3 rounded-lg border-2 border-dashed p-6 text-center transition-colors',
          dragging ? 'border-primary bg-accent' : 'border-input',
          className,
        )}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => void onDrop(event)}
      >
        <UploadCloudIcon className="size-8 text-muted-foreground" aria-hidden="true" />
        <p className="text-sm font-medium">
          {reading ? MESSAGES.upload.reading : MESSAGES.upload.dropHere}
        </p>
        <p className="text-xs text-muted-foreground">{MESSAGES.upload.or}</p>
        <div className="flex flex-wrap justify-center gap-2">
          <Button
            type="button"
            variant="outline"
            disabled={reading}
            onClick={() => folderInput.current?.click()}
          >
            <FolderOpenIcon />
            {MESSAGES.upload.chooseFolder}
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={reading}
            onClick={() => fileInput.current?.click()}
          >
            <FilesIcon />
            {MESSAGES.upload.chooseFiles}
          </Button>
        </div>
        <input
          ref={folderInput}
          type="file"
          multiple
          webkitdirectory=""
          hidden
          data-testid="folder-input"
          onChange={onInputChange}
        />
        <input
          ref={fileInput}
          type="file"
          multiple
          hidden
          data-testid="files-input"
          onChange={onInputChange}
        />
      </div>
      {failure === null ? null : <ErrorAlert message={failure} />}
    </div>
  );
}
