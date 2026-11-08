import { describe, expect, it, vi } from 'vitest'

import {
  ApiError,
  NetworkError,
  UnexpectedResponseError,
  ValidationError,
  requestJson,
} from './client'
import { createSample, getBackendHealth, getSample, listSamples } from './samples'
import { makeSample } from '../test/fakeApi'

function stubFetch(response: Response | Error) {
  const mock = vi.fn<typeof fetch>(() =>
    response instanceof Response ? Promise.resolve(response) : Promise.reject(response),
  )
  vi.stubGlobal('fetch', mock)
  return mock
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status })
}

describe('requestJson', () => {
  it('returns the parsed body of a successful response', async () => {
    stubFetch(json({ ok: true }))

    await expect(requestJson('/x')).resolves.toEqual({ ok: true })
  })

  it('turns a 422 into a ValidationError with per-field issues', async () => {
    stubFetch(
      json(
        {
          detail: [
            { loc: ['body', 'sample_name'], msg: 'Field required', type: 'missing' },
            { loc: ['body'], msg: 'Input should be a valid dictionary', type: 'dict_type' },
            { loc: ['query', 'page'], msg: 'Not a number', type: 'int_parsing' },
          ],
        },
        422,
      ),
    )

    const error = await requestJson('/x').catch((caught: unknown) => caught)

    expect(error).toBeInstanceOf(ValidationError)
    expect((error as ValidationError).status).toBe(422)
    expect((error as ValidationError).issues).toEqual([
      { field: 'sample_name', message: 'Field required' },
      { field: null, message: 'Input should be a valid dictionary' },
      { field: null, message: 'Not a number' },
    ])
  })

  it('uses a string detail as the message of other error statuses', async () => {
    stubFetch(json({ detail: 'Sample not found' }, 404))

    const error = await requestJson('/x').catch((caught: unknown) => caught)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).not.toBeInstanceOf(ValidationError)
    expect((error as ApiError).status).toBe(404)
    expect((error as ApiError).message).toBe('Sample not found')
  })

  it('falls back to a generic message when an error has no readable body', async () => {
    stubFetch(new Response(null, { status: 500 }))

    const error = await requestJson('/x').catch((caught: unknown) => caught)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(500)
    expect((error as ApiError).message).toBe('The API responded with HTTP 500.')
  })

  it('does not treat an HTML page served with 200 as data', async () => {
    stubFetch(new Response('<!doctype html><title>app</title>', { status: 200 }))

    await expect(requestJson('/api/v1/samples')).rejects.toBeInstanceOf(UnexpectedResponseError)
  })

  it('wraps a failed connection in a NetworkError', async () => {
    stubFetch(new TypeError('Failed to fetch'))

    await expect(requestJson('/x')).rejects.toBeInstanceOf(NetworkError)
  })

  it('rethrows an abort unchanged so callers can ignore it', async () => {
    const abort = new DOMException('Aborted', 'AbortError')
    stubFetch(abort)

    await expect(requestJson('/x')).rejects.toBe(abort)
  })
})

describe('requestJson headers', () => {
  const headerForms: Array<[string, HeadersInit]> = [
    ['a plain object', { 'X-Test': 'one' }],
    ['a Headers instance', new Headers({ 'X-Test': 'one' })],
    ['an array of header tuples', [['X-Test', 'one']]],
  ]
  const acceptOverrides: Array<[string, HeadersInit]> = [
    ['a plain object', { accept: 'text/plain' }],
    ['a Headers instance', new Headers({ ACCEPT: 'text/plain' })],
    ['an array of header tuples', [['Accept', 'text/plain']]],
  ]

  function sentHeaders(mock: ReturnType<typeof stubFetch>): Headers {
    return new Headers(mock.mock.calls[0]?.[1]?.headers)
  }

  it('sends the default Accept header when no headers are supplied', async () => {
    const mock = stubFetch(json({ ok: true }))

    await requestJson('/x')

    expect(sentHeaders(mock).get('Accept')).toBe('application/json')
  })

  it.each(headerForms)(
    'keeps headers supplied as %s and adds the default Accept header',
    async (_label, headers) => {
      const mock = stubFetch(json({ ok: true }))

      await requestJson('/x', { headers })

      expect(sentHeaders(mock).get('X-Test')).toBe('one')
      expect(sentHeaders(mock).get('Accept')).toBe('application/json')
    },
  )

  it.each(acceptOverrides)(
    'lets an Accept header supplied as %s override the default, whatever its case',
    async (_label, headers) => {
      const mock = stubFetch(json({ ok: true }))

      await requestJson('/x', { headers })

      const sent = sentHeaders(mock)
      expect(sent.get('Accept')).toBe('text/plain')
      expect([...sent.keys()].filter((name) => name === 'accept')).toHaveLength(1)
    },
  )

  it('does not modify a Headers instance supplied by the caller', async () => {
    stubFetch(json({ ok: true }))
    const supplied = new Headers({ 'X-Test': 'one' })

    await requestJson('/x', { headers: supplied })

    expect(supplied.has('Accept')).toBe(false)
  })

  it('still passes the other request options through', async () => {
    const mock = stubFetch(json({ ok: true }))

    await requestJson('/x', { method: 'POST', body: '{}', headers: [['X-Test', 'one']] })

    expect(mock.mock.calls[0]?.[1]?.method).toBe('POST')
    expect(mock.mock.calls[0]?.[1]?.body).toBe('{}')
  })
})

describe('sample endpoints', () => {
  it('lists samples from GET /api/v1/samples', async () => {
    const mock = stubFetch(json([makeSample()]))

    await expect(listSamples()).resolves.toEqual([makeSample()])
    expect(mock.mock.calls[0]?.[0]).toBe('/api/v1/samples')
  })

  it('rejects a list that is not an array', async () => {
    stubFetch(json({ samples: [] }))

    await expect(listSamples()).rejects.toBeInstanceOf(UnexpectedResponseError)
  })

  it('rejects a record that is missing a documented field', async () => {
    const { registered_at: _omitted, ...incomplete } = makeSample()
    stubFetch(json([incomplete]))

    await expect(listSamples()).rejects.toBeInstanceOf(UnexpectedResponseError)
  })

  it('encodes the id when fetching one sample', async () => {
    const mock = stubFetch(json(makeSample()))

    await getSample('a/b c')

    expect(mock.mock.calls[0]?.[0]).toBe('/api/v1/samples/a%2Fb%20c')
  })

  it('posts JSON to POST /api/v1/samples', async () => {
    const created = makeSample()
    const mock = stubFetch(json(created, 201))
    const input = {
      sample_name: created.sample_name,
      collection_location: created.collection_location,
      material_description: created.material_description,
      intended_application: created.intended_application,
    }

    await expect(createSample(input)).resolves.toEqual(created)

    const [path, init] = mock.mock.calls[0] ?? []
    expect(path).toBe('/api/v1/samples')
    expect(init?.method).toBe('POST')
    expect(new Headers(init?.headers).get('Content-Type')).toBe('application/json')
    expect(JSON.parse(String(init?.body))).toEqual(input)
  })

  it('reads the health response from /health', async () => {
    const mock = stubFetch(
      json({ status: 'healthy', application: 'MineMat AI API', version: '0.1.0' }),
    )

    await expect(getBackendHealth()).resolves.toMatchObject({ status: 'healthy' })
    expect(mock.mock.calls[0]?.[0]).toBe('/health')
  })
})
