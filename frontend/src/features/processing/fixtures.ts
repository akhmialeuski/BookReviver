import type {
  PageVersionSchema,
  ProcessorSchema,
  RecipeSchema,
  ScanSchema,
  StepSchema,
} from '@/api';
import { draftOf } from '@/features/processing/recipe';
import type { Processing } from '@/features/processing/useProcessing';
import { images } from '@/features/workspace/fixtures';

/**
 * Processors, recipes and versions as the tests of the processing workspace build them: the parameter schemas are the
 * ones the real plugins give, so the forms are tested on what they will draw.
 */

/** The JSON Schema of `geometry.deskew`, as its parameter model writes it. */
export const DESKEW_PARAMETERS = {
  additionalProperties: false,
  description: 'How far and how surely the page is turned.\n\n:ivar max_angle: Largest angle.',
  properties: {
    max_angle: {
      default: 5.0,
      description: 'Largest angle in degrees to look for, each way',
      exclusiveMinimum: 0,
      maximum: 45,
      title: 'Largest slant',
      type: 'number',
    },
    min_confidence: {
      default: 0.3,
      description: 'Confidence below which the page is left as it is',
      maximum: 1,
      minimum: 0,
      title: 'Least confidence',
      type: 'number',
    },
  },
  title: 'DeskewParams',
  type: 'object',
};

/** The JSON Schema of `split.spread`, as its parameter model writes it. */
export const SPREAD_PARAMETERS = {
  additionalProperties: false,
  description: 'Where the gutter is looked for.',
  properties: {
    search_band: {
      default: 0.3,
      description: 'Fraction of the width, in the middle, to search',
      exclusiveMinimum: 0,
      maximum: 1,
      title: 'Search width',
      type: 'number',
    },
    overlap_px: {
      default: 0,
      description: 'Pixels each half reaches over the cut',
      minimum: 0,
      title: 'Overlap',
      type: 'integer',
    },
    min_confidence: {
      default: 0.1,
      description: 'Confidence of the found gutter below which the cut is marked for a check',
      maximum: 1,
      minimum: 0,
      title: 'Least confidence',
      type: 'number',
    },
  },
  title: 'SpreadParams',
  type: 'object',
};

/** A processor of the catalogue. */
export function processor(key: string, overrides: Partial<ProcessorSchema> = {}): ProcessorSchema {
  return {
    key,
    version: '1',
    title: key,
    stage: 'geometry',
    scope: 'page',
    outputs: ['image'],
    parameters: {},
    editor: 'none',
    pool: 'cpu',
    ...overrides,
  };
}

/** `geometry.deskew`. */
export function deskew(overrides: Partial<ProcessorSchema> = {}): ProcessorSchema {
  return processor('geometry.deskew', {
    title: 'Deskew',
    parameters: DESKEW_PARAMETERS,
    editor: 'rotation',
    ...overrides,
  });
}

/** `split.spread`. */
export function spread(overrides: Partial<ProcessorSchema> = {}): ProcessorSchema {
  return processor('split.spread', {
    title: 'Spread',
    stage: 'page-split',
    scope: 'split',
    parameters: SPREAD_PARAMETERS,
    editor: 'line',
    ...overrides,
  });
}

/** `split.auto`, which decides for each scan and reads the choice a reader made for it. */
export function autoSplit(overrides: Partial<ProcessorSchema> = {}): ProcessorSchema {
  return processor('split.auto', {
    title: 'Automatic split',
    stage: 'page-split',
    scope: 'split',
    editor: 'split',
    ...overrides,
  });
}

/** `split.none`. */
export function whole(overrides: Partial<ProcessorSchema> = {}): ProcessorSchema {
  return processor('split.none', { title: 'Whole scan', stage: 'page-split', ...overrides });
}

/** A step of a recipe. */
export function step(processorKey: string, overrides: Partial<StepSchema> = {}): StepSchema {
  return { processor_key: processorKey, params: {}, enabled: true, ...overrides };
}

/** A recipe of a stage. */
export function recipe(id: string, overrides: Partial<RecipeSchema> = {}): RecipeSchema {
  return {
    id,
    project_id: 'project',
    stage: 'geometry',
    name: 'Deskew',
    steps: [step('geometry.deskew', { params: { max_angle: 5, min_confidence: 0.3 } })],
    active: true,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    ...overrides,
  };
}

/** A version of a page. */
export function version(id: string, overrides: Partial<PageVersionSchema> = {}): PageVersionSchema {
  return {
    id,
    page_id: 'page',
    stage: 'geometry',
    processor: { key: 'geometry.deskew', version: '1' },
    input_id: null,
    params: { max_angle: 5, min_confidence: 0.3 },
    transform: { kind: 'rotate', angle: 0, matrix: null, quad: null, mesh_key: null },
    data: {},
    review: null,
    state: 'ready',
    scale: 'full',
    edit_hash: '',
    tiles_ready: true,
    error: '',
    images: images(`version-${id}`),
    preview: null,
    created_at: '2026-10-01T00:00:00Z',
    ...overrides,
  };
}

/** A scan of a source with the size of its image. */
export function scan(id: string, widthPx: number, heightPx: number): ScanSchema {
  return {
    id,
    source_id: 'source',
    number: 0,
    source_label: '',
    facts: {
      width_px: widthPx,
      height_px: heightPx,
      color_mode: 'gray',
      dpi_x: null,
      dpi_y: null,
      bits_per_component: 8,
      image_format: 'PNG',
      width_mm: null,
      height_mm: null,
      has_text_layer: false,
      extra: {},
    },
    images: images(`scan-${id}`),
  };
}

/** The processing state of a screen, idle and clean, with any part of it changed. */
export function processing(overrides: Partial<Processing> = {}): Processing {
  const saved = recipe('r1');
  return {
    projectId: 'project',
    stage: 'geometry',
    available: true,
    ready: true,
    failed: false,
    catalogue: [deskew()],
    recipes: [saved],
    recipe: saved,
    chooseRecipe: () => undefined,
    steps: draftOf(saved),
    openId: undefined,
    open: () => undefined,
    shownStep: null,
    showStep: () => undefined,
    dirty: false,
    valid: true,
    move: () => undefined,
    toggle: () => undefined,
    remove: () => undefined,
    change: () => undefined,
    add: () => undefined,
    discard: () => undefined,
    preview: {
      on: false,
      toggle: () => undefined,
      blocked: null,
      shown: null,
      working: false,
      waiting: false,
      error: null,
    },
    ...overrides,
  };
}
