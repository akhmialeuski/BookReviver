import { useQueryClient } from '@tanstack/react-query';
import { createContext, useContext, useEffect, useState } from 'react';
import type { PlaceAddress } from '@/features/place/address';
import { cachedPlace, cachePlace, sendPlace } from '@/features/place/queries';
import { PlaceWriter } from '@/features/place/writer';

/**
 * Gives the screens of a book the writer of its place.
 *
 * The route of a stage and the route of the reading mode create the writer for their book, and the canvas and the strip
 * below them find it here to tell it when they move and to ask it where the reader left them. A screen rendered
 * without a route, such as a test of one component, finds no writer and works as if the place were not kept.
 */

/** The context that carries the writer; render it with the writer as its value. */
export const PlaceWriterContext = createContext<PlaceWriter | null>(null);

/** Find the writer of the place of the book on screen, or null outside a screen that keeps one. */
export function usePlaceWriter(): PlaceWriter | null {
  return useContext(PlaceWriterContext);
}

/**
 * Create the writer of the place of a book for as long as a screen is open, and tell it where the reader is.
 *
 * The writer starts from the place in the query cache, which the opening of the book put there, so the screen can
 * return the canvas and the strip to where they were. Give a book its own screen with a `key`, because the writer
 * belongs to one book.
 *
 * @param projectId The book.
 * @param address Where the reader is, or null while the screen has no address to write.
 */
export function useBookPlaceWriter(projectId: string, address: PlaceAddress | null): PlaceWriter {
  const queryClient = useQueryClient();
  const [writer] = useState(
    () =>
      new PlaceWriter({
        address,
        initial: cachedPlace(queryClient, projectId),
        send: (body, keepalive) => sendPlace(projectId, body, keepalive),
        remember: (place) => cachePlace(queryClient, projectId, place),
      }),
  );

  useEffect(() => {
    writer.start();
    return () => writer.stop();
  }, [writer]);

  useEffect(() => {
    writer.setAddress(address);
  }, [writer, address]);

  return writer;
}
