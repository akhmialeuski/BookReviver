import type OpenSeadragon from 'openseadragon';
import { useEffect, useState } from 'react';
import { SceneMapping } from '@/features/editors/mapping';
import type { Size } from '@/features/editors/shapes';

/**
 * What an editor draws over: the OpenSeadragon viewer and the one picture its edit lies on.
 *
 * The frame is worked out again on every change of the view, so a shape drawn from it follows the page when the reader
 * zooms, pans or resizes the canvas.
 */

/** The room the canvas leaves round the page while an editor is open, for its labels and handles, as a share of the height. */
export const EDITOR_ROOM_SHARE = 0.08;

/** The viewer of the canvas and the picture the edit lies on. */
export interface EditorScene {
  viewer: OpenSeadragon.Viewer;
  image: OpenSeadragon.TiledImage;
}

/** The scene at one moment: how to convert points, how big the stage is, and how far the view is turned. */
export interface SceneFrame {
  mapping: SceneMapping;
  /** The size of the viewer element in screen pixels, which the stage of the layer fills. */
  stage: Size;
  /** The size of the image in the pixels the edit is kept in. */
  size: Size;
  /** How far the view is turned clockwise, in degrees. */
  viewRotation: number;
}

// The viewer says it moved in all of these ways, and a layer that missed one would stand where the page was
const VIEW_EVENTS = ['viewport-change', 'resize', 'animation-finish'] as const;

/**
 * Follow the view of the viewer.
 *
 * @param scene The viewer and the picture.
 * @param editSize The size of the image in the pixels the edit is kept in, or null when they are the picture's own.
 */
export function useSceneFrame(scene: EditorScene, editSize: Size | null): SceneFrame {
  const { viewer, image } = scene;
  const [, setMoves] = useState(0);
  useEffect(() => {
    const moved = (): void => setMoves((count) => count + 1);
    for (const name of VIEW_EVENTS) {
      viewer.addHandler(name, moved);
    }
    return () => {
      if (!viewer.isDestroyed()) {
        for (const name of VIEW_EVENTS) {
          viewer.removeHandler(name, moved);
        }
      }
    };
  }, [viewer]);

  const content = image.getContentSize();
  const picture: Size = { width: content.x, height: content.y };
  const container = viewer.viewport.getContainerSize();
  return {
    mapping: new SceneMapping(image, editSize ?? picture, picture),
    stage: { width: container.x, height: container.y },
    size: editSize ?? picture,
    viewRotation: viewer.viewport.getRotation(true),
  };
}
