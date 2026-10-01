import type { UploadFile } from '@/features/projects/upload/files';

/**
 * The multipart body of an upload.
 *
 * The server learns the relative path of a file from the file name of its part, so every file is appended with its
 * path as the name, in list order. The generated client would append the bare `File`, which carries only the last
 * segment of the name and loses the folder a file came from.
 */

/** Name of the form field the upload route reads the files from. */
export const UPLOAD_FIELD = 'files';

/** Build the form of an upload, one part per file, named by its relative path, in the order of the list. */
export function buildUploadForm(files: readonly UploadFile[]): FormData {
  const form = new FormData();
  for (const { file, path } of files) {
    form.append(UPLOAD_FIELD, file, path);
  }
  return form;
}
