import type { BackendHealth, Sample, SampleInput } from '../types/sample'
import { UnexpectedResponseError, requestJson } from './client'

const SAMPLES_PATH = '/api/v1/samples'

const SAMPLE_KEYS = [
  'id',
  'sample_name',
  'material_description',
  'collection_location',
  'intended_application',
  'registered_at',
] as const

function isSample(value: unknown): value is Sample {
  if (typeof value !== 'object' || value === null) {
    return false
  }
  const record = value as Record<string, unknown>
  return SAMPLE_KEYS.every((key) => typeof record[key] === 'string')
}

function parseSample(payload: unknown): Sample {
  if (!isSample(payload)) {
    throw new UnexpectedResponseError(
      'The API returned a sample record in an unexpected format.',
    )
  }
  return payload
}

export async function listSamples(signal?: AbortSignal): Promise<Sample[]> {
  const payload = await requestJson(SAMPLES_PATH, { signal })
  if (!Array.isArray(payload)) {
    throw new UnexpectedResponseError(
      'The API returned the sample list in an unexpected format.',
    )
  }
  return payload.map(parseSample)
}

export async function getSample(
  sampleId: string,
  signal?: AbortSignal,
): Promise<Sample> {
  return parseSample(
    await requestJson(`${SAMPLES_PATH}/${encodeURIComponent(sampleId)}`, {
      signal,
    }),
  )
}

export async function createSample(
  input: SampleInput,
  signal?: AbortSignal,
): Promise<Sample> {
  return parseSample(
    await requestJson(SAMPLES_PATH, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
      signal,
    }),
  )
}

export async function getBackendHealth(
  signal?: AbortSignal,
): Promise<BackendHealth> {
  const payload = await requestJson('/health', { signal })
  if (
    typeof payload !== 'object' ||
    payload === null ||
    typeof (payload as Record<string, unknown>).status !== 'string'
  ) {
    throw new UnexpectedResponseError(
      'The API returned a health response in an unexpected format.',
    )
  }
  return payload as BackendHealth
}
