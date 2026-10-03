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
 * Give the schemas of the methods of a processor that has several, each with the fields of its own.
 *
 * The parameters of such a processor are a `oneOf`, one schema for each method, so the form shows the fields of the
 * method that is chosen and no others. Each is given as a reference to a definition of the schema, which is resolved
 * here.
 *
 * @param schema The form schema.
 * @returns The schema of each method in the order of the processor, none for a processor with a single method.
 */
export function methodsOf(schema: RJSFSchema): RJSFSchema[] {
  const definitions = isRecord(schema.$defs) ? schema.$defs : {};
  const found: RJSFSchema[] = [];
  for (const option of schema.oneOf ?? []) {
    if (!isRecord(option)) {
      continue;
    }
    const reference = typeof option.$ref === 'string' ? option.$ref.split('/').at(-1) : undefined;
    const resolved = reference === undefined ? option : definitions[reference];
    if (isRecord(resolved)) {
      found.push(resolved as RJSFSchema);
    }
  }
  return found;
}

/** Tell whether the schema has a field to draw, in the schema itself or in one of its methods. */
export function hasSettings(schema: RJSFSchema): boolean {
  return (
    Object.keys(propertiesOf(schema)).length > 0 ||
    methodsOf(schema).some((method) => Object.keys(propertiesOf(method)).length > 0)
  );
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
 * Give the title a field has in the form, which is the name a reader knows it by.
 *
 * @param schema The form schema.
 * @param name The name of the field, in the schema itself or in the schema of one of its methods.
 * @returns The title of the field, or its name when the schema gives none.
 */
export function fieldTitleOf(schema: RJSFSchema, name: string): string {
  for (const candidate of [schema, ...methodsOf(schema)]) {
    const property = propertiesOf(candidate)[name];
    if (isRecord(property) && typeof property.title === 'string') {
      return property.title;
    }
  }
  return name;
}

/** How a field that a page changes is labelled, from the title the form would give it. */
export type MarkLabel = (title: string) => string;

/** Choose the widget of each field of one schema, and the label of each field that is marked. */
function widgetsOf(schema: RJSFSchema, marked: ReadonlySet<string>, label: MarkLabel): UiSchema {
  const ui: UiSchema = {};
  for (const [name, property] of Object.entries(propertiesOf(schema))) {
    if (isRecord(property) && 'const' in property) {
      // The method of a method's own schema is fixed by the choice of the method, which the form draws itself
      ui[name] = { 'ui:widget': 'hidden' };
    } else if (sliderSpecOf(property) !== null) {
      ui[name] = { 'ui:widget': BOUNDED_NUMBER_WIDGET };
    }
    if (marked.has(name)) {
      const title =
        isRecord(property) && typeof property.title === 'string' ? property.title : name;
      ui[name] = { ...ui[name], 'ui:title': label(title) };
    }
  }
  return ui;
}

/**
 * Choose the widget of every field of the schema: a slider with an input for a number with both bounds, and none for the
 * name of a method that is fixed.
 *
 * @param schema The form schema.
 * @param marked The names of the fields a page changes for itself, whose label says so.
 * @param label How the label of a marked field is made from its title.
 * @returns The `uiSchema` of the form.
 */
export function uiSchemaOf(
  schema: RJSFSchema,
  marked: ReadonlySet<string> = new Set(),
  label: MarkLabel = (title) => title,
): UiSchema {
  const ui: UiSchema = {
    'ui:submitButtonOptions': { norender: true },
    ...widgetsOf(schema, marked, label),
  };
  const methods = methodsOf(schema);
  if (methods.length > 0) {
    // The name of the method is in the choice above its fields, so the fields need no heading of the same words
    ui.oneOf = methods.map((method) => ({
      ...widgetsOf(method, marked, label),
      'ui:options': { label: false },
    }));
  }
  return ui;
}

/**
 * Collect the default of every field that has one, the parameters a new step starts with.
 *
 * A processor with several methods starts with its first, which is the one it ran before it had several.
 *
 * @param schema The form schema.
 * @returns The defaults by field name; a field without a default is left out for the processor to fill in.
 */
export function defaultsOf(schema: RJSFSchema): Record<string, unknown> {
  const defaults: Record<string, unknown> = {};
  const [first] = methodsOf(schema);
  for (const [name, property] of Object.entries(propertiesOf(first ?? schema))) {
    if (isRecord(property) && 'default' in property) {
      defaults[name] = property.default;
    }
  }
  return defaults;
}

/**
 * Tell whether the values in a form fit the schema, so a value out of its bounds cannot be saved.
 *
 * Parameters that name no method are the ones of a recipe saved before the processor had methods, and the server reads
 * them as the first method, so they are checked as that.
 *
 * @param schema The form schema.
 * @param params The values of the form.
 */
export function fitsSchema(schema: RJSFSchema, params: Readonly<Record<string, unknown>>): boolean {
  const first = defaultsOf(schema).method;
  const named =
    first !== undefined && !('method' in params) ? { ...params, method: first } : params;
  return validator.validateFormData({ ...named }, schema).errors.length === 0;
}
