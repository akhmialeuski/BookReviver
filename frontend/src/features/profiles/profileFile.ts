import type { ImportProfileApiV1RecipeProfilesImportPostData, ProfileFileSchema } from '@/api';

/**
 * The file a profile is exchanged by: writing one for the download, and reading one the reader chose before it is sent.
 *
 * The server owns the format and checks every step, so the reading here only tells a file that is not a profile at all
 * (not JSON, not an object with the fields of a profile, or of a version this screen does not know) from one the server
 * should judge, and it does that before a request is spent on it.
 */

/** A profile file of any version the server still imports, as the body of the import names them. */
export type ProfileFile = ImportProfileApiV1RecipeProfilesImportPostData['body'];

/**
 * The versions of the format the server imports, which the server's `ProfileFileVersion` names. Each is checked against
 * the body of the import, so a version the server stops reading stops the build until it is taken out here too.
 */
const FILE_VERSIONS: ReadonlySet<number> = new Set([1, 2] satisfies ProfileFile['version'][]);
const FILE_SUFFIX = '.bookreviver-profile.json';
const FALLBACK_NAME = 'profile';
const MEDIA_TYPE = 'application/json';

/** Why a chosen file was turned away before it was sent. */
export const FileProblem = {
  NotJson: 'not-json',
  NotProfile: 'not-profile',
  Version: 'version',
} as const;

/** One reason of {@link FileProblem}. */
export type FileProblem = (typeof FileProblem)[keyof typeof FileProblem];

/** What reading a chosen file gave: the profile to send, or why it cannot be one. */
export type ReadFile = { ok: true; file: ProfileFile } | { ok: false; problem: FileProblem };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * Read the text of a chosen file as a profile file.
 *
 * @param text The content of the file.
 * @returns The file to send to the server, or the reason it is not a profile file.
 */
export function readProfileFile(text: string): ReadFile {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { ok: false, problem: FileProblem.NotJson };
  }
  if (
    !isRecord(parsed) ||
    typeof parsed.version !== 'number' ||
    typeof parsed.stage !== 'string' ||
    typeof parsed.name !== 'string' ||
    !Array.isArray(parsed.steps)
  ) {
    return { ok: false, problem: FileProblem.NotProfile };
  }
  if (!FILE_VERSIONS.has(parsed.version)) {
    return { ok: false, problem: FileProblem.Version };
  }
  // The shape of every step and the value of every field are checked by the server, which answers with what is wrong
  return { ok: true, file: parsed as unknown as ProfileFile };
}

/** Write a profile file as the text that is saved: indented, so a person can read and diff it, and ending in a newline. */
export function writeProfileFile(file: ProfileFileSchema): string {
  return `${JSON.stringify(file, null, 2)}\n`;
}

/** Give the name of the file a profile is saved to, made from the name of the profile. */
export function fileNameOf(profileName: string): string {
  const base = profileName
    .normalize('NFKD')
    .replace(/[^\p{L}\p{M}\p{N}]+/gu, '-')
    .replace(/^-+|-+$/g, '')
    .toLowerCase();
  return `${base === '' ? FALLBACK_NAME : base}${FILE_SUFFIX}`;
}

/** Hand the text to the browser as a file to save. */
export function saveTextFile(name: string, text: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: MEDIA_TYPE }));
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}
