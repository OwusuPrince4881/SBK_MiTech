/** The API could not be reached at all (server down, connection dropped). */
export class NetworkError extends Error {
  constructor(options?: ErrorOptions) {
    super('Could not reach the API.', options)
    this.name = 'NetworkError'
  }
}

/** The API answered with a non-success status. */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export interface ValidationIssue {
  /** Request-body field the issue refers to, or `null` for a general issue. */
  field: string | null
  message: string
}

/** HTTP 422: the API rejected the submitted values. */
export class ValidationError extends ApiError {
  readonly issues: ValidationIssue[]

  constructor(issues: ValidationIssue[]) {
    super(422, 'The API rejected the submitted values.')
    this.name = 'ValidationError'
    this.issues = issues
  }
}

/** The API answered successfully, but not with the JSON the client expects. */
export class UnexpectedResponseError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'UnexpectedResponseError'
  }
}

/** Matches by name rather than class so it holds across JavaScript realms. */
export function isAbortError(error: unknown): boolean {
  return (
    typeof error === 'object' &&
    error !== null &&
    'name' in error &&
    error.name === 'AbortError'
  )
}

type ParsedBody = { ok: true; value: unknown } | { ok: false }

function parseJson(text: string): ParsedBody {
  try {
    return { ok: true, value: JSON.parse(text) as unknown }
  } catch {
    return { ok: false }
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function validationIssues(detail: unknown[]): ValidationIssue[] {
  return detail.map((entry) => {
    const record = isRecord(entry) ? entry : {}
    const location = Array.isArray(record.loc) ? (record.loc as unknown[]) : []
    // FastAPI reports body problems as ["body", "<field>"]; ["body"] alone is
    // a problem with the whole body.
    const field =
      location[0] === 'body' && location.length > 1 ? String(location[1]) : null
    const message =
      typeof record.msg === 'string' ? record.msg : 'The value is not valid.'
    return { field, message }
  })
}

function toApiError(status: number, body: ParsedBody): ApiError {
  const detail = body.ok && isRecord(body.value) ? body.value.detail : undefined
  if (status === 422 && Array.isArray(detail)) {
    return new ValidationError(validationIssues(detail))
  }
  if (typeof detail === 'string' && detail.length > 0) {
    return new ApiError(status, detail)
  }
  return new ApiError(status, `The API responded with HTTP ${status}.`)
}

/**
 * Calls the API and returns the parsed JSON body. Failures are typed:
 * `NetworkError`, `ValidationError` (422), `ApiError` (other statuses) and
 * `UnexpectedResponseError` (success status, body is not JSON). Aborted
 * requests reject with the original `AbortError`.
 */
export async function requestJson(
  path: string,
  init: RequestInit = {},
): Promise<unknown> {
  // `new Headers()` accepts every HeadersInit form (plain object, Headers
  // instance, array of tuples), and header names are case-insensitive, so a
  // caller can override the default `Accept` however it spells the name.
  const headers = new Headers(init.headers)
  if (!headers.has('Accept')) {
    headers.set('Accept', 'application/json')
  }

  let text: string
  let response: Response
  try {
    response = await fetch(path, { ...init, headers })
    text = await response.text()
  } catch (error) {
    if (isAbortError(error)) {
      throw error
    }
    throw new NetworkError({ cause: error })
  }

  const body = parseJson(text)
  if (!response.ok) {
    throw toApiError(response.status, body)
  }
  if (!body.ok) {
    throw new UnexpectedResponseError(
      'The API returned a response that is not valid JSON.',
    )
  }
  return body.value
}
