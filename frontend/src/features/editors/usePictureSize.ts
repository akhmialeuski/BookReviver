import { useQuery } from '@tanstack/react-query';
import type { Size } from '@/features/editors/shapes';
import { type ImageSource, SourceKind } from '@/features/processing/compare';

/**
 * The size of a picture in its own pixels, read from the image information document of its tile pyramid.
 *
 * An editor of a step that has not run yet starts from the whole picture, and the step has not reported the size of what
 * it read. The pyramid does, at its full resolution, which is the pixels an edit is kept in. A plain image is a shrunk copy,
 * so its size is no answer, and a source that is not a pyramid gives none.
 */

/** The fields of the image information document that the size is read from. */
interface IiifInfo {
  width: number;
  height: number;
}

function isInfo(value: unknown): value is IiifInfo {
  const record = value as Partial<IiifInfo> | null;
  return typeof record?.width === 'number' && typeof record.height === 'number';
}

/**
 * Read the size of a picture.
 *
 * @param source The picture, or null when there is none.
 * @param enabled Whether the size is wanted, so that a screen that does not need it asks for nothing.
 * @returns The size, or null while it is read, when it could not be, and for a source that is not a pyramid.
 */
export function usePictureSize(source: ImageSource | null, enabled: boolean): Size | null {
  const url = source?.kind === SourceKind.Iiif ? source.url : null;
  const { data } = useQuery({
    queryKey: ['picture-size', url],
    queryFn: async (): Promise<Size> => {
      const response = await fetch(url ?? '');
      const info: unknown = await response.json();
      if (!response.ok || !isInfo(info)) {
        throw new Error(url ?? '');
      }
      return { width: info.width, height: info.height };
    },
    enabled: enabled && url !== null,
    // The pyramid of a picture never changes size
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  });
  return data ?? null;
}
