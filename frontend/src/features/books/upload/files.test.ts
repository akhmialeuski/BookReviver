import { describe, expect, it } from 'vitest';
import {
  addFiles,
  compareNatural,
  Direction,
  folderOf,
  groupByFolder,
  isSystemFile,
  kindOf,
  moveFile,
  moveFolder,
  type PickedFile,
  removeFile,
  SkipReason,
  sortNaturally,
  totalBytes,
  type UploadFile,
} from './files';

/**
 * The pure rules of the upload list: natural order, the filter of system files and unsupported types, and the moves
 * that rearrange a part of the book.
 */

function picked(...paths: string[]): PickedFile[] {
  return paths.map((path) => ({ path, file: new File(['x'], path.split('/').at(-1) ?? path) }));
}

function listOf(...paths: string[]): UploadFile[] {
  return addFiles([], picked(...paths)).files;
}

function pathsOf(files: readonly UploadFile[]): string[] {
  return files.map((file) => file.path);
}

describe('compareNatural', () => {
  it('orders numbers inside names by value, not digit by digit', () => {
    const sorted = ['10.tif', '2.tif', '1.tif', '100.tif'].sort(compareNatural);

    expect(sorted).toEqual(['1.tif', '2.tif', '10.tif', '100.tif']);
  });

  it('keeps the files of a folder together and orders folders by their names', () => {
    const sorted = ['vol2/1.tif', 'vol10/1.tif', 'vol1/9.tif', 'vol1/10.tif'].sort(compareNatural);

    expect(sorted).toEqual(['vol1/9.tif', 'vol1/10.tif', 'vol2/1.tif', 'vol10/1.tif']);
  });

  it('ignores letter case so Page 2 comes before page 10', () => {
    expect(['page 10.png', 'Page 2.png'].sort(compareNatural)).toEqual([
      'Page 2.png',
      'page 10.png',
    ]);
  });

  it('orders Cyrillic names with numbers too', () => {
    expect(['стр 10.jpg', 'стр 2.jpg'].sort(compareNatural)).toEqual(['стр 2.jpg', 'стр 10.jpg']);
  });

  it('gives paths that differ only in case a fixed order', () => {
    expect(compareNatural('a.tif', 'A.tif')).toBe(-compareNatural('A.tif', 'a.tif'));
    expect(compareNatural('a.tif', 'A.tif')).not.toBe(0);
  });
});

describe('isSystemFile', () => {
  it.each([
    'Thumbs.db',
    'scans/THUMBS.DB',
    'desktop.ini',
    '.DS_Store',
    'vol1/.DS_Store',
    '._001.tif',
    'vol1/._cover.jpg',
    'C:\\scans\\Thumbs.db',
  ])('%s is a system file', (path) => {
    expect(isSystemFile(path)).toBe(true);
  });

  it.each(['001.tif', 'thumbs.db.pdf', 'my.desktop.ini.png', 'vol._1/001.tif', 'a._b.tif'])(
    '%s is a part of the book',
    (path) => {
      expect(isSystemFile(path)).toBe(false);
    },
  );
});

describe('kindOf', () => {
  it.each([
    ['book.pdf', 'pdf'],
    ['BOOK.PDF', 'pdf'],
    ['a.djvu', 'djvu'],
    ['a.djv', 'djvu'],
    ['a.tif', 'tiff'],
    ['a.TIFF', 'tiff'],
    ['a.jpg', 'jpeg'],
    ['a.jpeg', 'jpeg'],
    ['a.jp2', 'jpeg-2000'],
    ['a.j2k', 'jpeg-2000'],
    ['dir.v1/a.png', 'png'],
  ])('%s is %s', (path, kind) => {
    expect(kindOf(path)).toBe(kind);
  });

  it.each(['notes.txt', 'archive.zip', 'noextension', 'pdf', '.pdf.bak', 'photo.gif'])(
    '%s is not accepted',
    (path) => {
      expect(kindOf(path)).toBeNull();
    },
  );
});

describe('folderOf', () => {
  it('returns the folder of a path and nothing for a bare name', () => {
    expect(folderOf('book/vol1/001.tif')).toBe('book/vol1');
    expect(folderOf('001.tif')).toBe('');
  });
});

describe('addFiles', () => {
  it('sorts a first pick naturally and gives each file its kind', () => {
    const { files, skipped } = addFiles([], picked('b/10.tif', 'b/2.tif', 'b/cover.pdf'));

    expect(pathsOf(files)).toEqual(['b/2.tif', 'b/10.tif', 'b/cover.pdf']);
    expect(files.map((file) => file.kind)).toEqual(['tiff', 'tiff', 'pdf']);
    expect(skipped).toEqual([]);
  });

  it('skips system files, unsupported types and repeated paths, and says why', () => {
    const { files, skipped } = addFiles(
      listOf('b/1.tif'),
      picked('b/Thumbs.db', 'b/._1.tif', 'b/notes.txt', 'b/1.tif', 'b/2.tif'),
    );

    expect(pathsOf(files)).toEqual(['b/1.tif', 'b/2.tif']);
    expect(skipped).toEqual([
      { path: 'b/Thumbs.db', reason: SkipReason.SystemFile },
      { path: 'b/._1.tif', reason: SkipReason.SystemFile },
      { path: 'b/notes.txt', reason: SkipReason.UnsupportedType },
      { path: 'b/1.tif', reason: SkipReason.Duplicate },
    ]);
  });

  it('appends a second pick after an order the person made instead of resorting', () => {
    const first = moveFile(listOf('1.tif', '2.tif'), '1.tif', Direction.Down);

    const { files } = addFiles(first, picked('0.tif'));

    expect(pathsOf(files)).toEqual(['2.tif', '1.tif', '0.tif']);
  });

  it('counts a path that appears twice in one pick once', () => {
    const { files, skipped } = addFiles([], picked('a.tif', 'a.tif'));

    expect(pathsOf(files)).toEqual(['a.tif']);
    expect(skipped).toEqual([{ path: 'a.tif', reason: SkipReason.Duplicate }]);
  });
});

describe('removeFile', () => {
  it('drops only the named file', () => {
    expect(pathsOf(removeFile(listOf('1.tif', '2.tif', '3.tif'), '2.tif'))).toEqual([
      '1.tif',
      '3.tif',
    ]);
  });
});

describe('moveFile', () => {
  it('swaps a file with its neighbour', () => {
    const files = listOf('1.tif', '2.tif', '3.tif');

    expect(pathsOf(moveFile(files, '2.tif', Direction.Up))).toEqual(['2.tif', '1.tif', '3.tif']);
    expect(pathsOf(moveFile(files, '2.tif', Direction.Down))).toEqual(['1.tif', '3.tif', '2.tif']);
  });

  it('leaves the list as it is at either end and for an unknown path', () => {
    const files = listOf('1.tif', '2.tif');

    expect(pathsOf(moveFile(files, '1.tif', Direction.Up))).toEqual(['1.tif', '2.tif']);
    expect(pathsOf(moveFile(files, '2.tif', Direction.Down))).toEqual(['1.tif', '2.tif']);
    expect(pathsOf(moveFile(files, 'missing.tif', Direction.Down))).toEqual(['1.tif', '2.tif']);
  });

  it('does not change the list it was given', () => {
    const files = listOf('1.tif', '2.tif');

    moveFile(files, '1.tif', Direction.Down);

    expect(pathsOf(files)).toEqual(['1.tif', '2.tif']);
  });
});

describe('groupByFolder', () => {
  it('splits the list into the runs of one folder in list order', () => {
    const groups = groupByFolder(listOf('v1/1.tif', 'v1/2.tif', 'v2/1.tif', 'cover.pdf'));

    expect(groups.map((group) => [group.folder, pathsOf(group.files)])).toEqual([
      ['', ['cover.pdf']],
      ['v1', ['v1/1.tif', 'v1/2.tif']],
      ['v2', ['v2/1.tif']],
    ]);
  });
});

describe('moveFolder', () => {
  const files = listOf('v1/1.tif', 'v1/2.tif', 'v2/1.tif', 'v3/1.tif');

  it('moves the whole run of a folder past its neighbour and keeps the order inside', () => {
    expect(pathsOf(moveFolder(files, 1, Direction.Up))).toEqual([
      'v2/1.tif',
      'v1/1.tif',
      'v1/2.tif',
      'v3/1.tif',
    ]);
    expect(pathsOf(moveFolder(files, 0, Direction.Down))).toEqual([
      'v2/1.tif',
      'v1/1.tif',
      'v1/2.tif',
      'v3/1.tif',
    ]);
  });

  it('leaves the list as it is at either end', () => {
    expect(pathsOf(moveFolder(files, 0, Direction.Up))).toEqual(pathsOf(files));
    expect(pathsOf(moveFolder(files, 2, Direction.Down))).toEqual(pathsOf(files));
  });
});

describe('sortNaturally', () => {
  it('puts a list the person reordered back into natural order', () => {
    const shuffled = moveFolder(listOf('v1/1.tif', 'v2/1.tif'), 0, Direction.Down);

    expect(pathsOf(sortNaturally(shuffled))).toEqual(['v1/1.tif', 'v2/1.tif']);
  });
});

describe('totalBytes', () => {
  it('adds the sizes of the files', () => {
    const files = addFiles(
      [],
      [
        { path: 'a.tif', file: new File(['12345'], 'a.tif') },
        { path: 'b.tif', file: new File(['123'], 'b.tif') },
      ],
    ).files;

    expect(totalBytes(files)).toBe(8);
  });
});
