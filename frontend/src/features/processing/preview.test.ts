import { describe, expect, it } from 'vitest';
import { version } from '@/features/processing/fixtures';
import { answersPreview, type PreviewRequest, previewKey } from '@/features/processing/preview';

const ASK: PreviewRequest = {
  pageId: 'page',
  stepIndex: 0,
  steps: [{ processor_key: 'geometry.deskew', params: { max_angle: 5, min_confidence: 0.3 } }],
};

describe('previewKey', () => {
  it('is the same for an ask whose parameters come in another order', () => {
    const reordered: PreviewRequest = {
      ...ASK,
      steps: [{ processor_key: 'geometry.deskew', params: { min_confidence: 0.3, max_angle: 5 } }],
    };

    expect(previewKey(reordered)).toBe(previewKey(ASK));
  });

  it('tells a changed parameter, a changed page and a changed step index apart', () => {
    const changedParameter: PreviewRequest = {
      ...ASK,
      steps: [{ processor_key: 'geometry.deskew', params: { max_angle: 6, min_confidence: 0.3 } }],
    };

    expect(previewKey(changedParameter)).not.toBe(previewKey(ASK));
    expect(previewKey({ ...ASK, pageId: 'other' })).not.toBe(previewKey(ASK));
    expect(previewKey({ ...ASK, stepIndex: 1 })).not.toBe(previewKey(ASK));
  });

  it('does not tell an ask apart by a step that is off or comes after the one asked for', () => {
    const more: PreviewRequest = {
      ...ASK,
      steps: [
        ...ASK.steps,
        { processor_key: 'geometry.crop', params: {}, enabled: false },
        { processor_key: 'geometry.dewarp', params: {} },
      ],
    };

    expect(previewKey(more)).toBe(previewKey(ASK));
  });

  it('tells a step switched off from one that is on', () => {
    const off: PreviewRequest = {
      ...ASK,
      steps: [{ processor_key: 'geometry.deskew', params: ASK.steps[0]?.params, enabled: false }],
    };

    expect(previewKey(off)).not.toBe(previewKey(ASK));
  });
});

describe('answersPreview', () => {
  const answer = version('v1', {
    scale: 'preview',
    preview: '/preview/v1.png',
    params: { max_angle: 5, min_confidence: 0.3 },
  });

  it('accepts the ready preview of the asked page made by the asked step', () => {
    expect(answersPreview(answer, ASK)).toBe(true);
  });

  it('accepts a version that holds more parameters than the ask names', () => {
    const partial: PreviewRequest = {
      ...ASK,
      steps: [{ processor_key: 'geometry.deskew', params: { max_angle: 5 } }],
    };

    expect(answersPreview(answer, partial)).toBe(true);
  });

  it('refuses a version of another page, of another processor, or with another value', () => {
    expect(answersPreview({ ...answer, page_id: 'other' }, ASK)).toBe(false);
    expect(
      answersPreview({ ...answer, processor: { key: 'geometry.crop', version: '1' } }, ASK),
    ).toBe(false);
    expect(answersPreview({ ...answer, params: { max_angle: 4, min_confidence: 0.3 } }, ASK)).toBe(
      false,
    );
  });

  it('refuses a full run, a version that is not ready and one with no picture', () => {
    expect(answersPreview({ ...answer, scale: 'full' }, ASK)).toBe(false);
    expect(answersPreview({ ...answer, state: 'failed' }, ASK)).toBe(false);
    expect(answersPreview({ ...answer, preview: null }, ASK)).toBe(false);
  });

  it('refuses when every step of the ask is off', () => {
    const off: PreviewRequest = {
      ...ASK,
      steps: [{ processor_key: 'geometry.deskew', params: {}, enabled: false }],
    };

    expect(answersPreview(answer, off)).toBe(false);
  });

  it('matches the processor of the last step that is on, not the last step', () => {
    const two: PreviewRequest = {
      ...ASK,
      stepIndex: 1,
      steps: [...ASK.steps, { processor_key: 'geometry.crop', params: {}, enabled: false }],
    };

    expect(answersPreview(answer, two)).toBe(true);
  });
});

describe('answersPreview for the step that places the content box', () => {
  const ask: PreviewRequest = {
    pageId: 'page',
    stepIndex: 0,
    steps: [
      {
        processor_key: 'geometry.normalize',
        params: { page_width: 0, page_height: 0, line_height: 0, margin_top: 150 },
      },
    ],
  };
  const made = (params: Record<string, unknown>) =>
    version('v1', {
      scale: 'preview',
      preview: '/preview/v1.png',
      params,
      processor: { key: 'geometry.normalize', version: '2' },
    });

  it('takes the size the book gave for a size the ask left at 0', () => {
    expect(
      answersPreview(
        made({ page_width: 1400, page_height: 2000, line_height: 31.5, margin_top: 150 }),
        ask,
      ),
    ).toBe(true);
  });

  it('still tells a margin that differs from the one asked for', () => {
    expect(
      answersPreview(
        made({ page_width: 1400, page_height: 2000, line_height: 31.5, margin_top: 90 }),
        ask,
      ),
    ).toBe(false);
  });

  it('does not take a size from the book for a step of another processor', () => {
    const other: PreviewRequest = {
      ...ask,
      steps: [{ processor_key: 'geometry.crop', params: { page_width: 0 } }],
    };

    expect(
      answersPreview(
        version('v2', {
          scale: 'preview',
          preview: '/preview/v2.png',
          params: { page_width: 5 },
          processor: { key: 'geometry.crop', version: '1' },
        }),
        other,
      ),
    ).toBe(false);
  });
});
