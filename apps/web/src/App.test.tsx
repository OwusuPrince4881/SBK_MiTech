import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { UserEvent } from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import App from './App'
import { installFakeApi, jsonResponse, makeSample, never } from './test/fakeApi'
import type { Responder } from './test/fakeApi'
import type { Sample, SampleField } from './types/sample'

// The notice text required by the product owner, written out in full so a
// change to the component's wording cannot silently pass.
const REQUIRED_NOTICE =
  'Registration record only. Suitability cannot be determined from registration data or photographs.'

const NORTH = makeSample({
  id: 'aaaaaaaa-0000-4000-8000-000000000001',
  sample_name: 'North pit sample 01',
  collection_location: 'Synthetic pit A',
  material_description: 'Synthetic weathered material',
  intended_application: 'Synthetic haul road base',
  registered_at: '2025-03-04T10:15:30.123456Z',
})
const SOUTH = makeSample({
  id: 'bbbbbbbb-0000-4000-8000-000000000002',
  sample_name: 'South haul road sample 02',
  collection_location: 'Synthetic pit B',
  material_description: 'Synthetic crushed material',
  intended_application: 'Synthetic embankment fill',
  registered_at: '2025-03-05T08:00:00Z',
})

const UNCERTAIN_MESSAGE =
  'Registration could not be confirmed. Check the registry before retrying.'

const FIELD_LABELS: Record<SampleField, string> = {
  sample_name: 'Sample name',
  collection_location: 'Collection location',
  material_description: 'Material description',
  intended_application: 'Intended application',
}

const VALID_ENTRY: Record<SampleField, string> = {
  sample_name: 'New synthetic sample',
  collection_location: 'Synthetic location C',
  material_description: 'Synthetic description',
  intended_application: 'Synthetic application',
}

async function enter(user: UserEvent, field: SampleField, value: string) {
  const control = screen.getByLabelText(FIELD_LABELS[field])
  await user.click(control)
  await user.paste(value)
}

async function fillForm(
  user: UserEvent,
  values: Record<SampleField, string> = VALID_ENTRY,
) {
  for (const field of Object.keys(values) as SampleField[]) {
    await enter(user, field, values[field])
  }
}

function field(name: SampleField): HTMLInputElement | HTMLTextAreaElement {
  return screen.getByLabelText(FIELD_LABELS[name])
}

function submit(user: UserEvent) {
  return user.click(screen.getByRole('button', { name: 'Register sample' }))
}

function registeredSamples() {
  return screen.getByRole('region', { name: 'Registered samples' })
}

describe('sample list', () => {
  it('shows a loading state while samples are being fetched', () => {
    installFakeApi({ list: never })

    render(<App />)

    expect(screen.getByText('Loading samples…')).toBeInTheDocument()
  })

  it('shows an empty state when no samples are registered', async () => {
    installFakeApi({ samples: [] })

    render(<App />)

    expect(await screen.findByText('No samples registered yet')).toBeInTheDocument()
    expect(screen.queryByText('Loading samples…')).not.toBeInTheDocument()
    expect(screen.queryByRole('list')).not.toBeInTheDocument()
  })

  it('shows an error state with the HTTP status, and recovers on retry', async () => {
    let attempts = 0
    const api = installFakeApi({
      list: () =>
        ++attempts === 1
          ? new Response(null, { status: 500 })
          : jsonResponse([NORTH]),
    })
    const user = userEvent.setup()

    render(<App />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Samples could not be loaded')
    expect(alert).toHaveTextContent('HTTP 500')

    await user.click(within(alert).getByRole('button', { name: 'Try again' }))

    expect(await within(registeredSamples()).findByText('North pit sample 01')).toBeInTheDocument()
    expect(screen.queryByText('Samples could not be loaded')).not.toBeInTheDocument()
    expect(api.calls('GET', '/api/v1/samples')).toHaveLength(2)
  })

  it('shows a network error when the API cannot be reached', async () => {
    installFakeApi({ list: () => Promise.reject(new TypeError('Failed to fetch')) })

    render(<App />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Samples could not be loaded')
    expect(alert).toHaveTextContent('The API could not be reached')
  })

  it('lists registered samples, newest first, with a count', async () => {
    installFakeApi({ samples: [NORTH, SOUTH] })

    render(<App />)

    const list = await within(registeredSamples()).findByRole('list')
    const rows = within(list).getAllByRole('listitem')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('South haul road sample 02')
    expect(rows[0]).toHaveTextContent('Synthetic pit B')
    expect(rows[0]).toHaveTextContent('ID bbbbbbbb')
    expect(rows[0]).toHaveTextContent('Registered 2025-03-05')
    expect(rows[1]).toHaveTextContent('North pit sample 01')
    expect(within(registeredSamples()).getByText('2 samples')).toBeInTheDocument()
  })
})

describe('sample detail', () => {
  it('asks the detail endpoint for the selected sample and shows its returned fields', async () => {
    const api = installFakeApi({ samples: [NORTH, SOUTH] })
    const user = userEvent.setup()
    render(<App />)

    expect(screen.getByText('No sample selected')).toBeInTheDocument()
    await user.click(
      await within(registeredSamples()).findByRole('button', {
        name: /South haul road sample 02/,
      }),
    )

    const detail = screen.getByRole('region', { name: 'Sample detail' })
    expect(
      await within(detail).findByRole('heading', { level: 3, name: 'South haul road sample 02' }),
    ).toBeInTheDocument()
    expect(api.calls('GET', `/api/v1/samples/${SOUTH.id}`)).toHaveLength(1)
    expect(within(detail).getByText(SOUTH.id)).toBeInTheDocument()
    expect(within(detail).getByText('Synthetic pit B')).toBeInTheDocument()
    expect(within(detail).getByText('Synthetic crushed material')).toBeInTheDocument()
    expect(within(detail).getByText('Synthetic embankment fill')).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /South haul road sample 02/ }),
    ).toHaveAttribute('aria-current', 'true')

    // Only fields the API returned are shown: no invented properties.
    expect(within(detail).getAllByRole('term').map((term) => term.textContent)).toEqual([
      'Sample ID',
      'Collection location',
      'Material description',
      'Intended application',
      'Registered (UTC)',
    ])
  })

  it('labels the registration time as UTC and renders it in UTC', async () => {
    installFakeApi({ samples: [NORTH] })
    const user = userEvent.setup()
    render(<App />)

    await user.click(
      await within(registeredSamples()).findByRole('button', { name: /North pit sample 01/ }),
    )

    const detail = screen.getByRole('region', { name: 'Sample detail' })
    expect(await within(detail).findByText('Registered (UTC)')).toBeInTheDocument()
    const time = within(detail).getByText('2025-03-04 10:15:30 UTC')
    expect(time.tagName).toBe('TIME')
    expect(time).toHaveAttribute('datetime', '2025-03-04T10:15:30.123456Z')
  })

  it('shows an error when the sample record cannot be loaded', async () => {
    installFakeApi({
      samples: [NORTH],
      detail: () => jsonResponse({ detail: 'Sample not found' }, 404),
    })
    const user = userEvent.setup()
    render(<App />)

    await user.click(
      await within(registeredSamples()).findByRole('button', { name: /North pit sample 01/ }),
    )

    const detail = screen.getByRole('region', { name: 'Sample detail' })
    const alert = await within(detail).findByRole('alert')
    expect(alert).toHaveTextContent('Sample record could not be loaded')
    expect(alert).toHaveTextContent('Sample not found')
  })

  it('does not present any engineering verdict wording', async () => {
    installFakeApi({ samples: [NORTH] })
    const user = userEvent.setup()
    render(<App />)
    await user.click(
      await within(registeredSamples()).findByRole('button', { name: /North pit sample 01/ }),
    )
    await screen.findByRole('heading', { level: 3, name: 'North pit sample 01' })

    const pageText = (document.body.textContent ?? '').replace(REQUIRED_NOTICE, '')

    expect(pageText).not.toMatch(
      /\b(pass(ed)?|approved|suitable|recommend(ed|ation)?|compliant|compliance|score|grade|rating|CBR)\b/i,
    )
  })
})

describe('registration form', () => {
  it('does not call the API for an invalid submission', async () => {
    const api = installFakeApi({ samples: [] })
    const user = userEvent.setup()
    render(<App />)
    await screen.findByText('No samples registered yet')

    await submit(user)

    expect(screen.getByText('Sample name is required.')).toBeInTheDocument()
    expect(screen.getByText('Collection location is required.')).toBeInTheDocument()
    expect(screen.getByText('Material description is required.')).toBeInTheDocument()
    expect(screen.getByText('Intended application is required.')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Correct the highlighted fields')
    expect(field('sample_name')).toHaveFocus()
    expect(field('sample_name')).toHaveAttribute('aria-invalid', 'true')
    expect(api.calls('POST', '/api/v1/samples')).toHaveLength(0)
  })

  it('rejects whitespace-only values without calling the API', async () => {
    const api = installFakeApi({ samples: [] })
    const user = userEvent.setup()
    render(<App />)
    await fillForm(user, { ...VALID_ENTRY, collection_location: '   \t  ' })

    await submit(user)

    expect(screen.getByText('Collection location is required.')).toBeInTheDocument()
    expect(api.calls('POST', '/api/v1/samples')).toHaveLength(0)
  })

  it('rejects a value one character over the limit, and accepts the limit itself', async () => {
    const api = installFakeApi({ samples: [] })
    const user = userEvent.setup()
    render(<App />)
    await fillForm(user, { ...VALID_ENTRY, sample_name: 'x'.repeat(201) })

    await submit(user)

    expect(
      screen.getByText('Sample name must be 200 characters or fewer (currently 201).'),
    ).toBeInTheDocument()
    expect(api.calls('POST', '/api/v1/samples')).toHaveLength(0)

    fireEvent.change(field('sample_name'), { target: { value: 'x'.repeat(200) } })
    await submit(user)

    await waitFor(() => expect(api.calls('POST', '/api/v1/samples')).toHaveLength(1))
  })

  it('sends exactly the four trimmed fields', async () => {
    const api = installFakeApi({ samples: [] })
    const user = userEvent.setup()
    render(<App />)
    await fillForm(user, {
      sample_name: '  New synthetic sample \t',
      collection_location: ' Synthetic location C ',
      material_description: '\nSynthetic description  ',
      intended_application: '  Synthetic application\n',
    })

    await submit(user)

    await waitFor(() => expect(api.calls('POST', '/api/v1/samples')).toHaveLength(1))
    const [request] = api.calls('POST', '/api/v1/samples')
    expect(request?.body).toEqual({
      sample_name: 'New synthetic sample',
      collection_location: 'Synthetic location C',
      material_description: 'Synthetic description',
      intended_application: 'Synthetic application',
    })
    const init = api.fetch.mock.calls.find(([, options]) => options?.method === 'POST')?.[1]
    expect(new Headers(init?.headers).get('Content-Type')).toBe('application/json')
  })

  it('refreshes the list after registration and shows the new sample', async () => {
    const api = installFakeApi({ samples: [NORTH] })
    const user = userEvent.setup()
    render(<App />)
    await within(registeredSamples()).findByText('North pit sample 01')
    await fillForm(user)

    await submit(user)

    expect(
      await within(registeredSamples()).findByRole('button', { name: /New synthetic sample/ }),
    ).toHaveAttribute('aria-current', 'true')
    expect(within(registeredSamples()).getByText('2 samples')).toBeInTheDocument()
    expect(api.calls('GET', '/api/v1/samples')).toHaveLength(2)

    const detail = screen.getByRole('region', { name: 'Sample detail' })
    expect(
      await within(detail).findByRole('heading', { level: 3, name: 'New synthetic sample' }),
    ).toBeInTheDocument()
    expect(screen.getByText(/Registered “New synthetic sample”/)).toBeInTheDocument()
    for (const name of Object.keys(FIELD_LABELS) as SampleField[]) {
      expect(field(name)).toHaveValue('')
    }
  })

  it('shows the API validation messages and keeps what was entered', async () => {
    installFakeApi({
      samples: [],
      create: () =>
        jsonResponse(
          {
            detail: [
              {
                type: 'string_too_long',
                loc: ['body', 'sample_name'],
                msg: 'String should have at most 200 characters',
                input: 'x',
                ctx: { max_length: 200 },
              },
              { type: 'dict_type', loc: ['body'], msg: 'Input should be a valid dictionary' },
            ],
          },
          422,
        ),
    })
    const user = userEvent.setup()
    render(<App />)
    const typed = { ...VALID_ENTRY, sample_name: '  Typed sample name ' }
    await fillForm(user, typed)

    await submit(user)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('The API rejected this registration')
    expect(alert).not.toHaveTextContent('could not be confirmed')
    expect(alert).toHaveTextContent('Input should be a valid dictionary')
    expect(screen.getByText('String should have at most 200 characters')).toBeInTheDocument()
    for (const name of Object.keys(typed) as SampleField[]) {
      expect(field(name)).toHaveValue(typed[name])
    }
    expect(screen.queryByText(/Registered “/)).not.toBeInTheDocument()
  })

  it('shows a network error and keeps what was entered', async () => {
    installFakeApi({
      samples: [],
      create: () => Promise.reject(new TypeError('Failed to fetch')),
    })
    const user = userEvent.setup()
    render(<App />)
    await fillForm(user)

    await submit(user)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(UNCERTAIN_MESSAGE)
    expect(alert).toHaveTextContent('No response was received from the API.')
    expect(alert).toHaveTextContent('Your entries have been kept.')
    for (const name of Object.keys(VALID_ENTRY) as SampleField[]) {
      expect(field(name)).toHaveValue(VALID_ENTRY[name])
    }
    expect(screen.getByRole('button', { name: 'Register sample' })).toBeEnabled()
  })

  it('shows a server error and keeps what was entered', async () => {
    installFakeApi({
      samples: [],
      create: () => new Response(null, { status: 500 }),
    })
    const user = userEvent.setup()
    render(<App />)
    await fillForm(user)

    await submit(user)

    expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 500')
    for (const name of Object.keys(VALID_ENTRY) as SampleField[]) {
      expect(field(name)).toHaveValue(VALID_ENTRY[name])
    }
  })

  it('sends only one request when the form is submitted twice quickly', async () => {
    let release!: (response: Response) => void
    const gate = new Promise<Response>((resolve) => {
      release = resolve
    })
    const api = installFakeApi({ samples: [], create: () => gate })
    const user = userEvent.setup()
    render(<App />)
    await fillForm(user)
    const form = screen.getByRole('button', { name: 'Register sample' }).closest('form')
    expect(form).not.toBeNull()

    fireEvent.submit(form as HTMLFormElement)
    fireEvent.submit(form as HTMLFormElement)

    await waitFor(() => expect(api.calls('POST', '/api/v1/samples')).toHaveLength(1))
    expect(screen.getByRole('button', { name: 'Registering…' })).toBeDisabled()
    const created: Sample = makeSample({ id: 'cccccccc-0000-4000-8000-000000000003' })
    release(jsonResponse(created, 201))
    expect(await screen.findByText(/Registered “/)).toBeInTheDocument()
    expect(api.calls('POST', '/api/v1/samples')).toHaveLength(1)
  })

  it('only offers the four fields the API stores', () => {
    installFakeApi({ samples: [] })

    render(<App />)

    const form = screen.getByRole('region', { name: 'Register a sample' })
    expect(within(form).getAllByRole('textbox').map((box) => box.getAttribute('name'))).toEqual([
      'sample_name',
      'collection_location',
      'material_description',
      'intended_application',
    ])
  })
})

describe('backend connection indicator', () => {
  it('shows checking, then connected with the API version', async () => {
    installFakeApi({ samples: [] })

    render(<App />)

    expect(screen.getByText('Checking API…')).toBeInTheDocument()
    expect(await screen.findByText('API connected')).toBeInTheDocument()
    expect(screen.getByText('v0.1.0')).toBeInTheDocument()
  })

  it('shows unavailable when the health check fails, and recovers on re-check', async () => {
    let healthy = false
    installFakeApi({
      samples: [],
      health: () =>
        healthy
          ? jsonResponse({ status: 'healthy', application: 'MineMat AI API', version: '0.1.0' })
          : Promise.reject(new TypeError('Failed to fetch')),
    })
    const user = userEvent.setup()
    render(<App />)

    expect(await screen.findByText('API unavailable')).toBeInTheDocument()

    healthy = true
    await user.click(screen.getByRole('button', { name: 'Check again' }))

    expect(await screen.findByText('API connected')).toBeInTheDocument()
  })

  it('treats a non-success health response as unavailable', async () => {
    installFakeApi({ samples: [], health: () => new Response(null, { status: 500 }) })

    render(<App />)

    expect(await screen.findByText('API unavailable')).toBeInTheDocument()
  })
})

describe('uncertain registration outcome', () => {
  const uncertainCases: Array<[string, Responder]> = [
    ['a lost connection', () => Promise.reject(new TypeError('Failed to fetch'))],
    ['an HTTP 500 response', () => new Response(null, { status: 500 })],
    ['an HTTP 502 response from the proxy', () => new Response(null, { status: 502 })],
    [
      'a success status with an unreadable body',
      () => new Response('<html>not json</html>', { status: 201 }),
    ],
    [
      'a success status with an unexpected JSON body',
      () => jsonResponse({ ok: true }, 201),
    ],
  ]

  it.each(uncertainCases)(
    'says the outcome could not be confirmed after %s, keeps the entries and does not retry',
    async (_label, create) => {
      const api = installFakeApi({ samples: [], create })
      const user = userEvent.setup()
      render(<App />)
      await fillForm(user)

      await submit(user)

      const alert = await screen.findByRole('alert')
      expect(within(alert).getByText(UNCERTAIN_MESSAGE)).toBeInTheDocument()
      expect(alert).toHaveTextContent('Your entries have been kept.')
      // It must not claim a definitive outcome either way, or advise retrying.
      expect(alert).not.toHaveTextContent(/not registered/i)
      expect(alert).not.toHaveTextContent(/rejected/i)
      expect(alert).not.toHaveTextContent(/try again/i)
      expect(screen.queryByText(/Registered “/)).not.toBeInTheDocument()
      for (const name of Object.keys(VALID_ENTRY) as SampleField[]) {
        expect(field(name)).toHaveValue(VALID_ENTRY[name])
      }
      expect(screen.getByRole('button', { name: 'Register sample' })).toBeEnabled()

      // No automatic retry: give any scheduled retry time to fire.
      await new Promise((resolve) => setTimeout(resolve, 100))
      expect(api.calls('POST', '/api/v1/samples')).toHaveLength(1)
    },
  )

  it('shows what the client observed beneath the message', async () => {
    installFakeApi({ samples: [], create: () => new Response(null, { status: 503 }) })
    const user = userEvent.setup()
    render(<App />)
    await fillForm(user)

    await submit(user)

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The API responded with an error (HTTP 503).',
    )
  })

  it('keeps a definitive API refusal distinct from an uncertain outcome', async () => {
    installFakeApi({
      samples: [],
      create: () => jsonResponse({ detail: 'Request entity too large' }, 413),
    })
    const user = userEvent.setup()
    render(<App />)
    await fillForm(user)

    await submit(user)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('The sample was not registered.')
    expect(alert).toHaveTextContent('Request entity too large')
    expect(alert).not.toHaveTextContent('could not be confirmed')
  })

  it('keeps the warning while the fields are edited, and a later submit is the user’s choice', async () => {
    const api = installFakeApi({
      samples: [],
      create: () => new Response(null, { status: 500 }),
    })
    const user = userEvent.setup()
    render(<App />)
    await fillForm(user)
    await submit(user)
    await screen.findByRole('alert')

    fireEvent.change(field('sample_name'), { target: { value: 'Edited sample name' } })

    expect(screen.getByRole('alert')).toHaveTextContent(UNCERTAIN_MESSAGE)
    expect(api.calls('POST', '/api/v1/samples')).toHaveLength(1)

    await submit(user)

    await waitFor(() => expect(api.calls('POST', '/api/v1/samples')).toHaveLength(2))
  })
})

describe('form-level validation banner', () => {
  const BANNER = 'Correct the highlighted fields, then register the sample.'

  it('goes away once every invalid field has been corrected, and not before', async () => {
    const api = installFakeApi({ samples: [] })
    const user = userEvent.setup()
    render(<App />)
    await submit(user)
    expect(screen.getByRole('alert')).toHaveTextContent(BANNER)

    fireEvent.change(field('sample_name'), { target: { value: 'Synthetic sample' } })
    fireEvent.change(field('collection_location'), { target: { value: 'Synthetic pit' } })
    fireEvent.change(field('material_description'), { target: { value: 'Synthetic soil' } })

    // Three of four corrected: the banner and the remaining message stay.
    expect(screen.getByRole('alert')).toHaveTextContent(BANNER)
    expect(screen.getByText('Intended application is required.')).toBeInTheDocument()
    // The messages of the corrected fields are gone.
    expect(screen.queryByText('Sample name is required.')).not.toBeInTheDocument()
    expect(screen.queryByText('Collection location is required.')).not.toBeInTheDocument()
    expect(screen.queryByText('Material description is required.')).not.toBeInTheDocument()

    fireEvent.change(field('intended_application'), { target: { value: 'Synthetic use' } })

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByText(/is required\./)).not.toBeInTheDocument()
    await submit(user)
    await waitFor(() => expect(api.calls('POST', '/api/v1/samples')).toHaveLength(1))
  })

  it('keeps a field’s message, updated as the user types, until that field is valid', async () => {
    installFakeApi({ samples: [] })
    const user = userEvent.setup()
    render(<App />)
    await submit(user)

    fireEvent.change(field('sample_name'), { target: { value: '   ' } })
    expect(screen.getByText('Sample name is required.')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent(BANNER)

    fireEvent.change(field('sample_name'), { target: { value: 'x'.repeat(201) } })
    expect(
      screen.getByText('Sample name must be 200 characters or fewer (currently 201).'),
    ).toBeInTheDocument()
    expect(field('sample_name')).toHaveAttribute('aria-invalid', 'true')

    fireEvent.change(field('sample_name'), { target: { value: 'x'.repeat(200) } })
    expect(screen.queryByText(/Sample name must be/)).not.toBeInTheDocument()
    expect(field('sample_name')).toHaveAttribute('aria-invalid', 'false')
    // Other fields are still invalid, so their messages and the banner remain.
    expect(screen.getByText('Collection location is required.')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent(BANNER)
  })

  it('does not drop the banner when a field that was already valid is edited', async () => {
    installFakeApi({ samples: [] })
    const user = userEvent.setup()
    render(<App />)
    await enter(user, 'collection_location', VALID_ENTRY.collection_location)
    await enter(user, 'material_description', VALID_ENTRY.material_description)
    await enter(user, 'intended_application', VALID_ENTRY.intended_application)
    await submit(user)
    expect(screen.getByRole('alert')).toHaveTextContent(BANNER)

    fireEvent.change(field('collection_location'), { target: { value: 'Another location' } })

    expect(screen.getByRole('alert')).toHaveTextContent(BANNER)
    expect(screen.getByText('Sample name is required.')).toBeInTheDocument()
  })

  it('clears the API-rejection banner when the reported field is corrected', async () => {
    installFakeApi({
      samples: [],
      create: () =>
        jsonResponse(
          {
            detail: [
              {
                type: 'string_too_long',
                loc: ['body', 'sample_name'],
                msg: 'String should have at most 200 characters',
              },
            ],
          },
          422,
        ),
    })
    const user = userEvent.setup()
    render(<App />)
    await fillForm(user)
    await submit(user)
    expect(await screen.findByRole('alert')).toHaveTextContent('The API rejected this registration')

    fireEvent.change(field('sample_name'), { target: { value: 'Corrected name' } })

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(
      screen.queryByText('String should have at most 200 characters'),
    ).not.toBeInTheDocument()
  })

  it('keeps an API message that is not tied to any field after the field is corrected', async () => {
    installFakeApi({
      samples: [],
      create: () =>
        jsonResponse(
          {
            detail: [
              {
                type: 'string_too_long',
                loc: ['body', 'sample_name'],
                msg: 'String should have at most 200 characters',
              },
              { type: 'dict_type', loc: ['body'], msg: 'Input should be a valid dictionary' },
            ],
          },
          422,
        ),
    })
    const user = userEvent.setup()
    render(<App />)
    await fillForm(user)
    await submit(user)
    await screen.findByRole('alert')
    expect(screen.getByText('String should have at most 200 characters')).toBeInTheDocument()

    fireEvent.change(field('sample_name'), { target: { value: 'Edited name' } })

    // The field's own message is gone, but the form-wide one (and so the
    // banner that carries it) stays: nothing the user can edit has fixed it.
    expect(screen.queryByText('String should have at most 200 characters')).not.toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Input should be a valid dictionary')
  })

  it('keeps a form-wide API message when no field is involved', async () => {
    installFakeApi({
      samples: [],
      create: () =>
        jsonResponse(
          { detail: [{ type: 'dict_type', loc: ['body'], msg: 'Input should be a valid dictionary' }] },
          422,
        ),
    })
    const user = userEvent.setup()
    render(<App />)
    await fillForm(user)
    await submit(user)
    await screen.findByRole('alert')

    fireEvent.change(field('sample_name'), { target: { value: 'Edited name' } })

    expect(screen.getByRole('alert')).toHaveTextContent('Input should be a valid dictionary')
  })
})

describe('engineering notice', () => {
  it('is visible while samples are loading', () => {
    installFakeApi({ list: never })

    render(<App />)

    expect(screen.getByText(REQUIRED_NOTICE)).toBeVisible()
  })

  it('is visible when the registry is empty', async () => {
    installFakeApi({ samples: [] })

    render(<App />)
    await screen.findByText('No samples registered yet')

    expect(screen.getByText(REQUIRED_NOTICE)).toBeVisible()
  })

  it('is visible when the list fails to load', async () => {
    installFakeApi({ list: () => new Response(null, { status: 500 }) })

    render(<App />)
    await screen.findByText('Samples could not be loaded')

    expect(screen.getByText(REQUIRED_NOTICE)).toBeVisible()
  })

  it('is visible with samples listed and a sample selected', async () => {
    installFakeApi({ samples: [NORTH] })
    const user = userEvent.setup()
    render(<App />)

    expect(screen.getByText(REQUIRED_NOTICE)).toBeVisible()
    await user.click(
      await within(registeredSamples()).findByRole('button', { name: /North pit sample 01/ }),
    )
    await screen.findByRole('heading', { level: 3, name: 'North pit sample 01' })

    expect(screen.getByText(REQUIRED_NOTICE)).toBeVisible()
    expect(screen.getByRole('note', { name: 'Engineering notice' })).toHaveTextContent(
      REQUIRED_NOTICE,
    )
  })

  it('is visible after a failed submission and after a successful one', async () => {
    let attempts = 0
    installFakeApi({
      samples: [],
      create: ({ body }) =>
        ++attempts === 1
          ? new Response(null, { status: 500 })
          : jsonResponse(makeSample(body as Partial<Sample>), 201),
    })
    const user = userEvent.setup()
    render(<App />)
    await fillForm(user)

    await submit(user)
    await screen.findByRole('alert')
    expect(screen.getByText(REQUIRED_NOTICE)).toBeVisible()

    await submit(user)
    await screen.findByText(/Registered “/)
    expect(screen.getByText(REQUIRED_NOTICE)).toBeVisible()
  })
})
