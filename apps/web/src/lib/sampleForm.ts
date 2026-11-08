import type { SampleField, SampleInput } from '../types/sample'

export interface FieldDefinition {
  name: SampleField
  label: string
  /** Mirrors the backend `SampleCreate` limit; counted in characters. */
  maxLength: number
  multiline: boolean
  hint?: string
}

/**
 * The fields the registry actually stores, in form order. The limits mirror
 * `SampleCreate` in the API and are initial application constraints, not
 * engineering standards. The API remains the authority and re-validates.
 */
export const SAMPLE_FIELDS: readonly FieldDefinition[] = [
  { name: 'sample_name', label: 'Sample name', maxLength: 200, multiline: false },
  {
    name: 'collection_location',
    label: 'Collection location',
    maxLength: 200,
    multiline: false,
  },
  {
    name: 'material_description',
    label: 'Material description',
    maxLength: 2000,
    multiline: true,
  },
  {
    name: 'intended_application',
    label: 'Intended application',
    maxLength: 2000,
    multiline: true,
    hint: 'Recorded as stated. It is not evaluated.',
  },
]

export type SampleFormValues = Record<SampleField, string>
export type FieldErrors = Partial<Record<SampleField, string>>

export const EMPTY_FORM_VALUES: SampleFormValues = {
  sample_name: '',
  collection_location: '',
  material_description: '',
  intended_application: '',
}

export type ValidationResult =
  | { ok: true; input: SampleInput }
  | { ok: false; errors: FieldErrors }

/**
 * Length in characters (Unicode code points), matching how the backend
 * counts. `String.length` would count UTF-16 units and over-count emoji.
 */
export function countCharacters(value: string): number {
  return Array.from(value).length
}

/**
 * Message for one field's value, or `undefined` if it is valid. The value is
 * trimmed first, as it will be when sent.
 */
export function validateSampleField(
  name: SampleField,
  value: string,
): string | undefined {
  const field = SAMPLE_FIELDS.find((candidate) => candidate.name === name)
  if (field === undefined) {
    throw new Error(`Unknown sample field: ${name}`)
  }
  const length = countCharacters(value.trim())
  if (length === 0) {
    return `${field.label} is required.`
  }
  if (length > field.maxLength) {
    return `${field.label} must be ${field.maxLength} characters or fewer (currently ${length}).`
  }
  return undefined
}

/** Trims every value, then checks it is present and within its limit. */
export function validateSampleForm(values: SampleFormValues): ValidationResult {
  const errors: FieldErrors = {}
  const trimmed = { ...EMPTY_FORM_VALUES }

  for (const field of SAMPLE_FIELDS) {
    trimmed[field.name] = values[field.name].trim()
    const message = validateSampleField(field.name, values[field.name])
    if (message !== undefined) {
      errors[field.name] = message
    }
  }

  if (Object.keys(errors).length > 0) {
    return { ok: false, errors }
  }
  return { ok: true, input: trimmed }
}
