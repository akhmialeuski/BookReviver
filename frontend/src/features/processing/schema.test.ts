import { describe, expect, it } from 'vitest';
import {
  DESKEW_METHODS_PARAMETERS,
  DESKEW_PARAMETERS,
  SPREAD_PARAMETERS,
} from '@/features/processing/fixtures';
import {
  BOUNDED_NUMBER_WIDGET,
  defaultsOf,
  fitsSchema,
  formSchemaOf,
  hasSettings,
  methodsOf,
  sliderSpecOf,
  snapToSlider,
  uiSchemaOf,
} from '@/features/processing/schema';

describe('formSchemaOf', () => {
  it('leaves out the class name and the docstring of the parameter model', () => {
    const schema = formSchemaOf(DESKEW_PARAMETERS);

    expect(schema.title).toBeUndefined();
    expect(schema.description).toBeUndefined();
    expect(Object.keys(schema.properties ?? {})).toEqual(['max_angle', 'min_confidence']);
  });

  it('keeps the title of every field, which is its label', () => {
    const schema = formSchemaOf(DESKEW_PARAMETERS);

    expect(schema.properties).toMatchObject({
      max_angle: { title: 'Largest slant' },
      min_confidence: { title: 'Least confidence' },
    });
  });
});

describe('sliderSpecOf', () => {
  it('starts a slider with an exclusive lower bound at the first step above it', () => {
    const spec = sliderSpecOf(DESKEW_PARAMETERS.properties.max_angle);

    expect(spec).toEqual({ min: 0.1, max: 45, step: 0.1 });
  });

  it('cuts a range of one into hundredths', () => {
    expect(sliderSpecOf(DESKEW_PARAMETERS.properties.min_confidence)).toEqual({
      min: 0,
      max: 1,
      step: 0.01,
    });
  });

  it('steps an integer by one', () => {
    expect(sliderSpecOf({ type: 'integer', minimum: 0, maximum: 200, title: 'Overlap' })).toEqual({
      min: 0,
      max: 200,
      step: 1,
    });
  });

  it('takes the step the schema names', () => {
    expect(sliderSpecOf({ type: 'number', minimum: 0, maximum: 10, multipleOf: 0.5 })).toEqual({
      min: 0,
      max: 10,
      step: 0.5,
    });
  });

  it('gives no slider to a number with only one bound', () => {
    expect(sliderSpecOf(SPREAD_PARAMETERS.properties.overlap_px)).toBeNull();
  });

  it('gives no slider to a field that is not a number', () => {
    expect(sliderSpecOf({ type: 'string', minLength: 1, maxLength: 4 })).toBeNull();
    expect(sliderSpecOf(true)).toBeNull();
  });
});

describe('snapToSlider', () => {
  const spec = { min: 0.1, max: 45, step: 0.1 };

  it('puts a value on the nearest step without a float tail', () => {
    expect(snapToSlider(2.34, spec)).toBe(2.3);
    expect(snapToSlider(0.30000000000000004, spec)).toBe(0.3);
  });

  it('holds a value inside the bounds', () => {
    expect(snapToSlider(-3, spec)).toBe(0.1);
    expect(snapToSlider(100, spec)).toBe(45);
  });
});

describe('uiSchemaOf', () => {
  it('draws the numbers with both bounds as sliders and the others as they come', () => {
    const ui = uiSchemaOf(formSchemaOf(SPREAD_PARAMETERS));

    expect(ui.search_band).toEqual({ 'ui:widget': BOUNDED_NUMBER_WIDGET });
    expect(ui.min_confidence).toEqual({ 'ui:widget': BOUNDED_NUMBER_WIDGET });
    expect(ui.overlap_px).toBeUndefined();
  });

  it('draws no submit button, since the panel saves the recipe', () => {
    expect(uiSchemaOf(formSchemaOf(DESKEW_PARAMETERS))['ui:submitButtonOptions']).toEqual({
      norender: true,
    });
  });

  it('copes with a schema that has no fields', () => {
    expect(() => uiSchemaOf({ type: 'object' })).not.toThrow();
  });
});

describe('defaultsOf', () => {
  it('collects the default of every field', () => {
    expect(defaultsOf(formSchemaOf(SPREAD_PARAMETERS))).toEqual({
      search_band: 0.3,
      overlap_px: 0,
      min_confidence: 0.1,
    });
  });

  it('leaves out a field with no default', () => {
    expect(defaultsOf({ type: 'object', properties: { width: { type: 'integer' } } })).toEqual({});
  });
});

describe('fitsSchema', () => {
  const schema = formSchemaOf(DESKEW_PARAMETERS);

  it('accepts values inside the bounds, the bounds included', () => {
    expect(fitsSchema(schema, { max_angle: 5, min_confidence: 0.3 })).toBe(true);
    expect(fitsSchema(schema, { max_angle: 45, min_confidence: 0 })).toBe(true);
  });

  it('refuses a value above its upper bound', () => {
    expect(fitsSchema(schema, { max_angle: 46, min_confidence: 0.3 })).toBe(false);
  });

  it('refuses the value an exclusive lower bound excludes', () => {
    expect(fitsSchema(schema, { max_angle: 0, min_confidence: 0.3 })).toBe(false);
  });

  it('refuses a value of the wrong type', () => {
    expect(fitsSchema(schema, { max_angle: 'wide', min_confidence: 0.3 })).toBe(false);
  });
});

describe('methods of a processor', () => {
  const schema = formSchemaOf(DESKEW_METHODS_PARAMETERS);

  it('lists the schema of each method, resolved from the definitions', () => {
    expect(methodsOf(schema).map((method) => method.title)).toEqual([
      'Projection of the ink',
      'Long straight lines',
      'Baselines of the text',
    ]);
  });

  it('lists none for a processor with a single method', () => {
    expect(methodsOf(formSchemaOf(DESKEW_PARAMETERS))).toEqual([]);
  });

  it('has settings to draw in the methods though it has no field of its own', () => {
    expect(schema.properties).toBeUndefined();
    expect(hasSettings(schema)).toBe(true);
    expect(hasSettings({ type: 'object' })).toBe(false);
  });

  it('draws the fields of each method by its own widgets, with no heading and no field for the method', () => {
    const ui = uiSchemaOf(schema);

    expect(ui.oneOf).toHaveLength(3);
    expect(ui.oneOf?.[0]).toEqual({
      max_angle: { 'ui:widget': BOUNDED_NUMBER_WIDGET },
      min_confidence: { 'ui:widget': BOUNDED_NUMBER_WIDGET },
      method: { 'ui:widget': 'hidden' },
      'ui:options': { label: false },
    });
    expect(ui.oneOf?.[1]?.min_line_share).toEqual({ 'ui:widget': BOUNDED_NUMBER_WIDGET });
  });

  it('starts a new step with the first method and its defaults', () => {
    expect(defaultsOf(schema)).toEqual({
      max_angle: 5,
      min_confidence: 0.3,
      method: 'projection',
    });
  });

  it('checks the values against the schema of the method they name', () => {
    expect(fitsSchema(schema, { method: 'hough', min_line_share: 0.5 })).toBe(true);
    expect(fitsSchema(schema, { method: 'hough', min_line_share: 2 })).toBe(false);
    expect(fitsSchema(schema, { method: 'baselines', min_line_share: 0.5 })).toBe(false);
  });

  it('checks values that name no method as the first one, which is how the server reads them', () => {
    expect(fitsSchema(schema, { max_angle: 5, min_confidence: 0.3 })).toBe(true);
    expect(fitsSchema(schema, { max_angle: 50, min_confidence: 0.3 })).toBe(false);
  });
});
