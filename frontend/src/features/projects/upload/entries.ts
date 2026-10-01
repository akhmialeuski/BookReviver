import type { PickedFile } from '@/features/projects/upload/files';

/**
 * Reading the files of a folder that was dropped on the page.
 *
 * A drop gives `DataTransferItem`s, and `webkitGetAsEntry()` turns one into a file or directory entry that is walked
 * through the File and Directory Entries API. Two details decide whether every file arrives. The items of a drop are
 * valid only during the drop event, so every entry is taken before the first `await`. And a directory reader hands
 * out its entries in batches, 100 at a time in Chromium, and signals the end by an empty batch, so it is read until
 * that empty batch and not just once.
 */

/** What the walk needs of an entry; a `FileSystemEntry` has it, and so does a plain object in a test. */
export interface EntryNode {
  readonly isFile: boolean;
  readonly isDirectory: boolean;
  readonly fullPath: string;
}

export interface FileNode extends EntryNode {
  file(success: (file: File) => void, failure: (error: unknown) => void): void;
}

export interface DirectoryNode extends EntryNode {
  createReader(): ReaderNode;
}

export interface ReaderNode {
  readEntries(success: (entries: EntryNode[]) => void, failure: (error: unknown) => void): void;
}

function isFileNode(entry: EntryNode): entry is FileNode {
  return entry.isFile && 'file' in entry;
}

function isDirectoryNode(entry: EntryNode): entry is DirectoryNode {
  return entry.isDirectory && 'createReader' in entry;
}

function readBatch(reader: ReaderNode): Promise<EntryNode[]> {
  return new Promise((resolve, reject) => reader.readEntries(resolve, reject));
}

function readFile(entry: FileNode): Promise<File> {
  return new Promise((resolve, reject) => entry.file(resolve, reject));
}

/** Read every entry of a directory, however many batches the reader splits them into. */
export async function readAllEntries(reader: ReaderNode): Promise<EntryNode[]> {
  const entries: EntryNode[] = [];
  for (let batch = await readBatch(reader); batch.length > 0; batch = await readBatch(reader)) {
    entries.push(...batch);
  }
  return entries;
}

/** Return the path of an entry relative to the dropped folder's parent, without the leading slash. */
function relativePath(entry: EntryNode): string {
  return entry.fullPath.replace(/^\//, '');
}

/** Walk an entry and return every file under it, a directory's files in the order the reader gave them. */
export async function walkEntry(entry: EntryNode): Promise<PickedFile[]> {
  if (isFileNode(entry)) {
    return [{ path: relativePath(entry), file: await readFile(entry) }];
  }
  if (!isDirectoryNode(entry)) {
    return [];
  }
  const children = await readAllEntries(entry.createReader());
  const walked = await Promise.all(children.map((child) => walkEntry(child)));
  return walked.flat();
}

/**
 * Return the files of everything dropped, folders walked to their last file.
 *
 * An item the browser cannot give as an entry, which a synthetic drop can be, falls back to its plain file.
 */
export async function collectDroppedFiles(items: DataTransferItemList): Promise<PickedFile[]> {
  // Both lookups happen before any await, because the list is emptied once the drop event has finished
  const roots = Array.from(items)
    .filter((item) => item.kind === 'file')
    .map((item) => ({ entry: item.webkitGetAsEntry(), file: item.getAsFile() }));

  const walked = await Promise.all(
    roots.map(async ({ entry, file }): Promise<PickedFile[]> => {
      if (entry !== null) {
        return walkEntry(entry);
      }
      return file === null ? [] : [{ path: file.name, file }];
    }),
  );
  return walked.flat();
}

/** Return the files of a file input, using the relative path a folder choice gives every file. */
export function filesOfInput(files: Iterable<File> | null): PickedFile[] {
  return Array.from(files ?? [], (file) => ({
    path: file.webkitRelativePath === '' ? file.name : file.webkitRelativePath,
    file,
  }));
}
