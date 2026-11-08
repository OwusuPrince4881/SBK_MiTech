import { vi } from 'vitest'
import type { Mock } from 'vitest'

import type { Sample } from '../types/sample'

/** Synthetic records only. Nothing here represents a real sample. */
export function makeSample(overrides: Partial<Sample> = {}): Sample {
  return {
    id: '3f2a9c1e-5b7d-4c8a-9e61-0a1b2c3d4e5f',
    sample_name: 'Synthetic sample 01',
    material_description: 'Synthetic placeholder material',
    collection_location: 'Synthetic location A',
    intended_application: 'Synthetic placeholder application',
    registered_at: '2025-03-04T10:15:30.123456Z',
    ...overrides,
  }
}

export interface RecordedRequest {
  path: string
  method: string
  body: unknown
}

export type Responder = (request: RecordedRequest) => Response | Promise<Response>

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** A request that never completes, to hold a component in its loading state. */
export const never: Responder = () => new Promise<Response>(() => {})

export interface FakeApiOptions {
  samples?: Sample[]
  health?: Responder
  list?: Responder
  detail?: Responder
  create?: Responder
}

export interface FakeApi {
  fetch: Mock<typeof fetch>
  requests: RecordedRequest[]
  /** Requests made with the given method to a path (query-free). */
  calls: (method: string, path: string) => RecordedRequest[]
}

const SAMPLES_PATH = '/api/v1/samples'

function abortError(): DOMException {
  return new DOMException('The operation was aborted.', 'AbortError')
}

/** Resolves like `fetch` would, but rejects with AbortError once aborted. */
function respectAbort<T>(promise: Promise<T>, signal?: AbortSignal | null): Promise<T> {
  if (!signal) {
    return promise
  }
  if (signal.aborted) {
    return Promise.reject(abortError())
  }
  return new Promise<T>((resolve, reject) => {
    signal.addEventListener('abort', () => reject(abortError()), { once: true })
    promise.then(resolve, reject)
  })
}

/**
 * Replaces `fetch` with an in-memory fake of the sample API. Every request is
 * recorded. `create` defaults to registering the posted fields as a new
 * sample; override any responder to simulate errors. The stub is removed
 * automatically after each test (`unstubGlobals` in the Vitest config).
 */
export function installFakeApi(options: FakeApiOptions = {}): FakeApi {
  const samples = [...(options.samples ?? [])]
  const requests: RecordedRequest[] = []
  let nextId = 1

  const defaults: Required<Omit<FakeApiOptions, 'samples'>> = {
    health: () =>
      jsonResponse({ status: 'healthy', application: 'MineMat AI API', version: '0.1.0' }),
    list: () => jsonResponse(samples),
    detail: ({ path }) => {
      const found = samples.find((sample) => path.endsWith(`/${sample.id}`))
      return found
        ? jsonResponse(found)
        : jsonResponse({ detail: 'Sample not found' }, 404)
    },
    create: ({ body }) => {
      const created = makeSample({
        id: `00000000-0000-4000-8000-${String(nextId++).padStart(12, '0')}`,
        registered_at: `2025-03-05T09:00:${String(nextId).padStart(2, '0')}.000000Z`,
        ...(body as Partial<Sample>),
      })
      samples.push(created)
      return jsonResponse(created, 201)
    },
  }
  const responders = { ...defaults, ...stripUndefined(options) }

  const fetchMock = vi.fn<typeof fetch>((input, init) => {
    const path = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : undefined
    const request: RecordedRequest = { path, method, body }
    requests.push(request)

    let responder: Responder | undefined
    if (path === '/health' && method === 'GET') {
      responder = responders.health
    } else if (path === SAMPLES_PATH && method === 'GET') {
      responder = responders.list
    } else if (path === SAMPLES_PATH && method === 'POST') {
      responder = responders.create
    } else if (path.startsWith(`${SAMPLES_PATH}/`) && method === 'GET') {
      responder = responders.detail
    }
    if (!responder) {
      return Promise.resolve(jsonResponse({ detail: 'Not Found' }, 404))
    }
    return respectAbort(Promise.resolve(responder(request)), init?.signal)
  })
  vi.stubGlobal('fetch', fetchMock)

  return {
    fetch: fetchMock,
    requests,
    calls: (method, path) =>
      requests.filter((request) => request.method === method && request.path === path),
  }
}

function stripUndefined(options: FakeApiOptions): Partial<Omit<FakeApiOptions, 'samples'>> {
  const { samples: _samples, ...rest } = options
  return Object.fromEntries(
    Object.entries(rest).filter(([, value]) => value !== undefined),
  ) as Partial<Omit<FakeApiOptions, 'samples'>>
}
