import { describe, expect, it } from 'vitest';
import { addFiles } from './files';
import { buildUploadForm, UPLOAD_FIELD } from './upload-form';

/**
 * The body sent to the upload route: every part is named by the relative path and the order is kept.
 */

describe('buildUploadForm', () => {
  it('sends each file under its relative path in list order', () => {
    const { files } = addFiles(
      [],
      [
        { path: 'book/vol2/1.tif', file: new File(['b'], '1.tif') },
        { path: 'book/vol1/1.tif', file: new File(['a'], '1.tif') },
      ],
    );

    const parts = buildUploadForm(files).getAll(UPLOAD_FIELD);

    expect(parts.map((part) => (part instanceof File ? part.name : part))).toEqual([
      'book/vol1/1.tif',
      'book/vol2/1.tif',
    ]);
  });

  it('keeps the content of the file', async () => {
    const { files } = addFiles([], [{ path: 'a/p.png', file: new File(['pixels'], 'p.png') }]);

    const [part] = buildUploadForm(files).getAll(UPLOAD_FIELD);

    expect(part instanceof File ? await part.text() : null).toBe('pixels');
  });
});
