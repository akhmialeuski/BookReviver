import type { RJSFSchema, UiSchema } from '@rjsf/utils';
import validator from '@rjsf/validator-ajv8';

/**
 * What the parameter form of a processor takes from the JSON Schema the catalogue gives: the schema the form draws,
 * the widget of each bounded number, the slider a bound pair makes, the defaults of a new step, and whether the values
 * in the form fit the schema.
 *
 * The schema is the one of the processor's parameter model, so the label of a field is its `title` and the hint under
 * it its `description`. The model itself has a `title`, its class name, and a `description`, its docstring, which are
 * written for the code and never reach a reader, so the form schema leaves both out.
 */

/** The name of the widget that draws a number with both bounds as a slider with an input. */
export const BOUNDED_NUMBER_WIDGET = 'bounded-number';

/** The range and the step of a slider. */
export interface SliderSpec {
  min: number;
  max: number;
  step: number;
}

/** A slider spans about this many steps, whatever the range, so a drag has a fine hand and a short way. */
const STEPS_PER_RANGE = 100;

type Properties = Record<string, unknown>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function propertiesOf(schema: RJSFSchema): Properties {
  return isRecord(schema.properties) ? schema.properties : {};
}

/**
 * Give the schema the form draws for the parameters of a processor.
 *
 * @param parameters The JSON Schema of the processor, as the catalogue gives it.
 * @returns The same schema without the title and the description of the model, so the form draws no heading and no
 * text of its own above the fields.
 */
export function formSchemaOf(parameters: Readonly<Record<string, unknown>>): RJSFSchema {
  const { title: _title, description: _description, ...schema } = parameters;
  return schema as RJSFSchema;
}

/** Round a step to the digits it has, so ten steps of 0.1 are 1 and not 0.9999999999999999. */
function digitsOf(step: number): number {
  return Math.max(0, Math.ceil(-Math.log10(step)));
}

/**
 * Work out the slider of a number that has both bounds.
 *
 * An exclusive lower bound is the first step above it, and the step is the schema's `multipleOf`, one for an integer,
 * or the power of ten that cuts the range into about a hundred steps.
 *
 * @param property The schema of one field.
 * @returns The slider, or null for a field that is no number or lacks a bound.
 */
export function sliderSpecOf(property: unknown): SliderSpec | null {
  if (!isRecord(property) || (property.type !== 'number' && property.type !== 'integer')) {
    return null;
  }
  const low = property.minimum ?? property.exclusiveMinimum;
  const high = property.maximum ?? property.exclusiveMaximum;
  if (typeof low !== 'number' || typeof high !== 'number' || high <= low) {
    return null;
  }
  let step = 1;
  if (typeof property.multipleOf === 'number' && property.multipleOf > 0) {
    step = property.multipleOf;
  } else if (property.type === 'number') {
    step = 10 ** Math.floor(Math.log10((high - low) / STEPS_PER_RANGE));
  }
  const digits = digitsOf(step);
  const min = typeof property.minimum === 'number' ? low : Number((low + step).toFixed(digits));
  const max = typeof property.maximum === 'number' ? high : Number((high - step).toFixed(digits));
  return { min, max, step };
}

/** Give the position of a number on its slider: the nearest step, held inside the bounds. */
export function snapToSlider(value: number, spec: SliderSpec): number {
  const steps = Math.round((value - spec.min) / spec.step);
  const snapped = Number((spec.min + steps * spec.step).toFixed(digitsOf(spec.step)));
  return Math.min(Math.max(snapped, spec.min), spec.max);
}

/**
 * Choose the widget of every field of the schema: a slider with an input for a number with both bounds.
 *
 * @param schema The form schema.
 * @returns The `uiSchema` of the form.
 */
export function uiSchemaOf(schema: RJSFSchema): UiSchema {
  const ui: UiSchema = { 'ui:submitButtonOptions': { norender: true } };
  for (const [name, property] of Object.entries(propertiesOf(schema))) {
    if (sliderSpecOf(property) !== null) {
      ui[name] = { 'ui:widget': BOUNDED_NUMBER_WIDGET };
    }
  }
  return ui;
}

/**
 * Collect the default of every field that has one, the parameters a new step starts with.
 *
 * @param schema The form schema.
 * @returns The defaults by field name; a field without a default is left out for the processor to fill in.
 */
export function defaultsOf(schema: RJSFSchema): Record<string, unknown> {
  const defaults: Record<string, unknown> = {};
  for (const [name, property] of Object.entries(propertiesOf(schema))) {
    if (isRecord(property) && 'default' in property) {
      defaults[name] = property.default;
    }
  }
  return defaults;
}

/**
 * Tell whether the values in a form fit the schema, so a value out of its bounds cannot be saved.
 *
 * @param schema The form schema.
 * @param params The values of the form.
 */
export function fitsSchema(schema: RJSFSchema, params: Readonly<Record<string, unknown>>): boolean {
  return validator.validateFormData({ ...params }, schema).errors.length === 0;
}
