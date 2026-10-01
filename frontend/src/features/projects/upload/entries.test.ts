import { describe, expect, it } from 'vitest';
import {
  type DirectoryNode,
  type EntryNode,
  type FileNode,
  filesOfInput,
  readAllEntries,
  walkEntry,
} from './entries';

/**
 * Reading of dropped folders against fake entries, including a directory reader that gives 100 entries per call as
 * Chromium's does.
 */

const CHROMIUM_BATCH_SIZE = 100;

function fileEntry(fullPath: string): FileNode {
  const name = fullPath.split('/').at(-1) ?? fullPath;
  return {
    isFile: true,
    isDirectory: false,
    fullPath,
    file: (success) => success(new File(['x'], name)),
  };
}

function directoryEntry(
  fullPath: string,
  children: EntryNode[],
  batchSize = CHROMIUM_BATCH_SIZE,
): DirectoryNode {
  return {
    isFile: false,
    isDirectory: true,
    fullPath,
    createReader: () => {
      let offset = 0;
      return {
        readEntries: (success) => {
          const batch = children.slice(offset, offset + batchSize);
          offset += batch.length;
          success(batch);
        },
      };
    },
  };
}

function numberedFiles(folder: string, count: number): FileNode[] {
  return Array.from({ length: count }, (_, index) =>
    fileEntry(`${folder}/${String(index).padStart(4, '0')}.tif`),
  );
}

describe('readAllEntries', () => {
  it.each([0, 1, 99, 100, 101, 250, 1000])(
    'returns all %d entries of a directory read in batches of 100',
    async (count) => {
      const directory = directoryEntry('/book', numberedFiles('/book', count));

      const entries = await readAllEntries(directory.createReader());

      expect(entries).toHaveLength(count);
    },
  );

  it('stops on the empty batch, not on a short one', async () => {
    const directory = directoryEntry('/book', numberedFiles('/book', 150), 60);

    expect(await readAllEntries(directory.createReader())).toHaveLength(150);
  });

  it('rejects when the reader fails', async () => {
    const failing = {
      readEntries: (_: unknown, failure: (error: unknown) => void) => failure(new Error('denied')),
    };

    await expect(readAllEntries(failing)).rejects.toThrow('denied');
  });
});

describe('walkEntry', () => {
  it('returns every file of a dropped folder of more than 100 files', async () => {
    const folder = directoryEntry('/book', numberedFiles('/book', 250));

    const picked = await walkEntry(folder);

    expect(picked).toHaveLength(250);
    expect(picked.at(0)?.path).toBe('book/0000.tif');
    expect(picked.at(-1)?.path).toBe('book/0249.tif');
  });

  it('descends into subfolders and keeps the relative paths', async () => {
    const folder = directoryEntry('/book', [
      fileEntry('/book/cover.pdf'),
      directoryEntry('/book/vol1', numberedFiles('/book/vol1', 120)),
      directoryEntry('/book/vol2', numberedFiles('/book/vol2', 3)),
    ]);

    const picked = await walkEntry(folder);

    expect(picked).toHaveLength(124);
    expect(picked.map((file) => file.path)).toContain('book/vol1/0119.tif');
    expect(picked.map((file) => file.path)).toContain('book/vol2/0002.tif');
    expect(picked.map((file) => file.path)).toContain('book/cover.pdf');
  });

  it('returns a single dropped file under its bare name', async () => {
    expect(await walkEntry(fileEntry('/page.png'))).toMatchObject([{ path: 'page.png' }]);
  });

  it('returns nothing for an empty folder', async () => {
    expect(await walkEntry(directoryEntry('/empty', []))).toEqual([]);
  });
});

describe('filesOfInput', () => {
  it('uses the relative path of a chosen folder and the name of a chosen file', () => {
    const inFolder = new File(['x'], '001.tif');
    Object.defineProperty(inFolder, 'webkitRelativePath', { value: 'book/vol1/001.tif' });
    // A browser gives an empty relative path to a file that was not chosen as part of a folder, a bare File does not
    const single = new File(['x'], 'page.png');
    Object.defineProperty(single, 'webkitRelativePath', { value: '' });

    const picked = filesOfInput([inFolder, single]);

    expect(picked.map((file) => file.path)).toEqual(['book/vol1/001.tif', 'page.png']);
  });

  it('returns nothing when no files were chosen', () => {
    expect(filesOfInput(null)).toEqual([]);
  });
});
