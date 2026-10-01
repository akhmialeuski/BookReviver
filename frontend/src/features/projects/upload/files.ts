import type { FileType } from '@/api';

/**
 * The files chosen for an upload, before they are sent.
 *
 * Every pick, whether a chosen folder, a dropped folder or single files, becomes a list of `UploadFile` in the order
 * the book will have: the server imports in the order it receives the files and adds the pages of the scans to the
 * end of the book in that order, and sorts nothing itself. So the order is decided here, in natural order by default
 * (`2.tif` before `10.tif`), and the person can still remove a file or move a file or a whole folder.
 *
 * The tests of what is a system file and what is a supported type mirror the backend (`SystemFile` and `FileType`
 * in `domain/enums.py`), which stays the authority and rejects by the same rules. Showing them first lets the person
 * see what will not be sent.
 */

/** A file as a picker or a drop gave it: the file and its path relative to the chosen folder. */
export interface PickedFile {
  path: string;
  file: File;
}

/** A file that will be sent, with the kind of book source it makes. The path is unique in a list. */
export interface UploadFile extends PickedFile {
  kind: FileType;
}

/** Why a picked file will not be sent. */
export const SkipReason = {
  SystemFile: 'system-file',
  UnsupportedType: 'unsupported-type',
  Duplicate: 'duplicate',
} as const;

/** One reason of {@link SkipReason}. */
export type SkipReason = (typeof SkipReason)[keyof typeof SkipReason];

export interface SkippedFile {
  path: string;
  reason: SkipReason;
}

/** The files of one folder that follow each other in the list; the unit of moving a part of the book. */
export interface FolderGroup {
  /** Path of the folder, or an empty string for files chosen one by one. */
  folder: string;
  files: UploadFile[];
}

/** Which way an item moves in the list. */
export const Direction = { Up: -1, Down: 1 } as const;

/** One direction of {@link Direction}. */
export type Direction = (typeof Direction)[keyof typeof Direction];

// Suffixes of each accepted type, lower case with the dot, as the backend's FILE_TYPE_SUFFIXES lists them
const FILE_TYPE_SUFFIXES: readonly (readonly [FileType, readonly string[]])[] = [
  ['pdf', ['.pdf']],
  ['djvu', ['.djvu', '.djv']],
  ['tiff', ['.tif', '.tiff']],
  ['jpeg', ['.jpg', '.jpeg']],
  ['jpeg-2000', ['.jp2', '.j2k']],
  ['png', ['.png']],
];

// Files an operating system adds to a folder, compared by the whole lower-case name
const SYSTEM_FILE_NAMES: ReadonlySet<string> = new Set(['thumbs.db', 'desktop.ini', '.ds_store']);
// macOS writes the resource fork of 001.tif as ._001.tif, which has the suffix of the image it accompanies
const APPLE_DOUBLE_PREFIX = '._';

const PATH_SEPARATOR = '/';

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });

function segmentsOf(path: string): string[] {
  return path.replaceAll('\\', PATH_SEPARATOR).split(PATH_SEPARATOR);
}

/** Return the file name, the last segment of a relative path. */
export function fileNameOf(path: string): string {
  return segmentsOf(path).at(-1) ?? path;
}

/** Return the folder of a relative path, or an empty string for a bare file name. */
export function folderOf(path: string): string {
  return segmentsOf(path).slice(0, -1).join(PATH_SEPARATOR);
}

/** Tell whether a path is a system file of an operating system, which is not a part of the book. */
export function isSystemFile(path: string): boolean {
  const name = fileNameOf(path).toLowerCase();
  return SYSTEM_FILE_NAMES.has(name) || name.startsWith(APPLE_DOUBLE_PREFIX);
}

/** Return the kind of source a file makes by its suffix, or null for a type the import does not accept. */
export function kindOf(path: string): FileType | null {
  const name = fileNameOf(path).toLowerCase();
  const dot = name.lastIndexOf('.');
  if (dot < 0) {
    return null;
  }
  const suffix = name.slice(dot);
  return FILE_TYPE_SUFFIXES.find(([, suffixes]) => suffixes.includes(suffix))?.[0] ?? null;
}

/**
 * Compare two relative paths in natural order, folder by folder.
 *
 * Digit runs compare as numbers and letter case is ignored, so `page 2.png` comes before `page 10.png` and
 * `vol1/9.tif` before `vol2/1.tif`. Paths that differ only in case or accents fall back to a plain comparison so
 * the order is the same on every run.
 */
export function compareNatural(a: string, b: string): number {
  const left = segmentsOf(a);
  const right = segmentsOf(b);
  const shared = Math.min(left.length, right.length);
  for (let index = 0; index < shared; index += 1) {
    const order = collator.compare(left[index] ?? '', right[index] ?? '');
    if (order !== 0) {
      return order;
    }
  }
  if (left.length !== right.length) {
    return left.length - right.length;
  }
  if (a === b) {
    return 0;
  }
  return a < b ? -1 : 1;
}

/** Return the files sorted in natural order of their paths. */
export function sortNaturally(files: readonly UploadFile[]): UploadFile[] {
  return [...files].sort((a, b) => compareNatural(a.path, b.path));
}

/**
 * Add picked files to a list: system files and unsupported types are skipped, so are paths already in the list,
 * and the new files, in natural order, join the end so an order the person made is kept.
 */
export function addFiles(
  current: readonly UploadFile[],
  picked: readonly PickedFile[],
): { files: UploadFile[]; skipped: SkippedFile[] } {
  const known = new Set(current.map((file) => file.path));
  const added: UploadFile[] = [];
  const skipped: SkippedFile[] = [];
  for (const { path, file } of picked) {
    const kind = kindOf(path);
    if (isSystemFile(path)) {
      skipped.push({ path, reason: SkipReason.SystemFile });
    } else if (known.has(path)) {
      skipped.push({ path, reason: SkipReason.Duplicate });
    } else if (kind === null) {
      skipped.push({ path, reason: SkipReason.UnsupportedType });
    } else {
      known.add(path);
      added.push({ path, file, kind });
    }
  }
  return { files: [...current, ...sortNaturally(added)], skipped };
}

/** Return the list without the file at `path`. */
export function removeFile(files: readonly UploadFile[], path: string): UploadFile[] {
  return files.filter((file) => file.path !== path);
}

function swap<T>(items: readonly T[], from: number, to: number): T[] {
  const result = [...items];
  const moved = result[from];
  const other = result[to];
  if (moved === undefined || other === undefined) {
    return result;
  }
  result[from] = other;
  result[to] = moved;
  return result;
}

/** Move one file a place up or down; at an end of the list the list stays as it is. */
export function moveFile(
  files: readonly UploadFile[],
  path: string,
  direction: Direction,
): UploadFile[] {
  const from = files.findIndex((file) => file.path === path);
  return from < 0 ? [...files] : swap(files, from, from + direction);
}

/** Split the list into the runs of files that share a folder, in list order. */
export function groupByFolder(files: readonly UploadFile[]): FolderGroup[] {
  const groups: FolderGroup[] = [];
  for (const file of files) {
    const folder = folderOf(file.path);
    const last = groups.at(-1);
    if (last?.folder === folder) {
      last.files.push(file);
    } else {
      groups.push({ folder, files: [file] });
    }
  }
  return groups;
}

/** Move a whole folder, the run of files at `groupIndex`, past the neighbouring run. */
export function moveFolder(
  files: readonly UploadFile[],
  groupIndex: number,
  direction: Direction,
): UploadFile[] {
  const groups = groupByFolder(files);
  return swap(groups, groupIndex, groupIndex + direction).flatMap((group) => group.files);
}

/** Return the total size of the files in bytes. */
export function totalBytes(files: readonly UploadFile[]): number {
  return files.reduce((sum, { file }) => sum + file.size, 0);
}
