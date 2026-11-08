import { describe, expect, it } from 'vitest'

import {
  ApiError,
  NetworkError,
  UnexpectedResponseError,
  ValidationError,
} from '../api/client'
import { isOutcomeUncertain } from './errors'
import { formatUtcDate, formatUtcTimestamp, registrationTime, shortId } from './format'
import {
  EMPTY_FORM_VALUES,
  countCharacters,
  validateSampleField,
  validateSampleForm,
} from './sampleForm'

const VALID = {
  sample_name: 'Synthetic sample',
  collection_location: 'Synthetic location',
  material_description: 'Synthetic material',
  intended_application: 'Synthetic application',
}

describe('formatUtcTimestamp', () => {
  it('renders a Z timestamp, dropping fractional seconds', () => {
    expect(formatUtcTimestamp('2025-03-04T10:15:30.123456Z')).toBe('2025-03-04 10:15:30 UTC')
  })

  it('converts an offset timestamp to UTC', () => {
    expect(formatUtcTimestamp('2025-03-04T12:15:30+02:00')).toBe('2025-03-04 10:15:30 UTC')
  })

  it('returns unparseable input unchanged instead of guessing', () => {
    expect(formatUtcTimestamp('not a date')).toBe('not a date')
  })
})

describe('other formatting helpers', () => {
  it('formats the UTC date', () => {
    expect(formatUtcDate('2025-03-04T23:59:59Z')).toBe('2025-03-04')
  })

  it('shortens an id to eight characters', () => {
    expect(shortId('3f2a9c1e-5b7d-4c8a-9e61-0a1b2c3d4e5f')).toBe('3f2a9c1e')
  })

  it('sorts unparseable times as oldest', () => {
    expect(registrationTime('nonsense')).toBe(0)
    expect(registrationTime('2025-03-04T10:15:30Z')).toBeGreaterThan(0)
  })
})

describe('countCharacters', () => {
  it('counts code points, as the backend does, not UTF-16 units', () => {
    expect(countCharacters('abc')).toBe(3)
    expect(countCharacters('😀😀')).toBe(2)
    expect('😀😀'.length).toBe(4)
  })
})

describe('validateSampleForm', () => {
  it('trims every value and returns exactly the four fields', () => {
    const result = validateSampleForm({
      sample_name: '  Synthetic sample ',
      collection_location: '\tSynthetic location',
      material_description: 'Synthetic material\n',
      intended_application: ' Synthetic application ',
    })

    expect(result).toEqual({ ok: true, input: VALID })
  })

  it('requires every field and treats whitespace-only as empty', () => {
    const result = validateSampleForm({ ...EMPTY_FORM_VALUES, sample_name: '   ' })

    expect(result).toEqual({
      ok: false,
      errors: {
        sample_name: 'Sample name is required.',
        collection_location: 'Collection location is required.',
        material_description: 'Material description is required.',
        intended_application: 'Intended application is required.',
      },
    })
  })

  it.each([
    ['sample_name', 200],
    ['collection_location', 200],
    ['material_description', 2000],
    ['intended_application', 2000],
  ] as const)('accepts %s at %i characters and rejects one more', (field, limit) => {
    const atLimit = validateSampleForm({ ...VALID, [field]: 'x'.repeat(limit) })
    const overLimit = validateSampleForm({ ...VALID, [field]: 'x'.repeat(limit + 1) })

    expect(atLimit.ok).toBe(true)
    expect(overLimit.ok).toBe(false)
    if (!overLimit.ok) {
      expect(overLimit.errors[field]).toContain(`${limit} characters or fewer`)
      expect(overLimit.errors[field]).toContain(`currently ${limit + 1}`)
    }
  })

  it('applies the limit after trimming', () => {
    const result = validateSampleForm({ ...VALID, sample_name: `  ${'x'.repeat(200)}  ` })

    expect(result.ok).toBe(true)
  })

  it('counts emoji as single characters against the limit', () => {
    const result = validateSampleForm({ ...VALID, sample_name: '😀'.repeat(200) })

    expect(result.ok).toBe(true)
  })
})

describe('validateSampleField', () => {
  it('returns nothing for a valid value, trimming first', () => {
    expect(validateSampleField('sample_name', '  Synthetic sample  ')).toBeUndefined()
  })

  it('reports a missing or whitespace-only value as required', () => {
    expect(validateSampleField('collection_location', '')).toBe('Collection location is required.')
    expect(validateSampleField('collection_location', ' \t ')).toBe('Collection location is required.')
  })

  it('reports a value over the limit with its current length', () => {
    expect(validateSampleField('sample_name', 'x'.repeat(201))).toBe(
      'Sample name must be 200 characters or fewer (currently 201).',
    )
    expect(validateSampleField('sample_name', 'x'.repeat(200))).toBeUndefined()
  })

  it('agrees with validateSampleForm for every field', () => {
    const result = validateSampleForm({ ...EMPTY_FORM_VALUES, sample_name: 'x'.repeat(201) })

    expect(result.ok).toBe(false)
    if (!result.ok) {
      expect(result.errors.sample_name).toBe(validateSampleField('sample_name', 'x'.repeat(201)))
      expect(result.errors.intended_application).toBe(
        validateSampleField('intended_application', ''),
      )
    }
  })
})

describe('isOutcomeUncertain', () => {
  it.each([
    ['a lost connection', new NetworkError()],
    ['an HTTP 500', new ApiError(500, 'Server error')],
    ['an HTTP 503', new ApiError(503, 'Unavailable')],
    ['a success response that cannot be read', new UnexpectedResponseError('Not JSON')],
    ['an unexpected exception', new Error('boom')],
    ['a non-error value', 'boom'],
  ])('treats %s as uncertain', (_label, error) => {
    expect(isOutcomeUncertain(error)).toBe(true)
  })

  it.each([
    ['a 422 validation error', new ValidationError([])],
    ['an HTTP 404', new ApiError(404, 'Not found')],
    ['an HTTP 413', new ApiError(413, 'Too large')],
  ])('treats %s as a definitive refusal', (_label, error) => {
    expect(isOutcomeUncertain(error)).toBe(false)
  })
})
