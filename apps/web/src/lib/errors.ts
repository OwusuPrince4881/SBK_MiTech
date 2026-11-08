import { ApiError, NetworkError, UnexpectedResponseError } from '../api/client'

export const REGISTRATION_UNCERTAIN_MESSAGE =
  'Registration could not be confirmed. Check the registry before retrying.'

/**
 * Whether the client cannot tell if a registration reached the database.
 *
 * Definitive answers are the API's own 4xx responses (including 422
 * validation errors): the request was refused. Everything else is uncertain:
 * a lost connection or a 5xx may come after the server committed the sample,
 * and a success response with an unreadable body means it was probably stored.
 */
export function isOutcomeUncertain(error: unknown): boolean {
  if (error instanceof ApiError) {
    return error.status >= 500
  }
  return true
}

/**
 * Technical reason shown beneath the uncertain-outcome message. It states what
 * the client observed and gives no advice to retry.
 */
export function describeUncertainFailure(error: unknown): string {
  if (error instanceof NetworkError) {
    return 'No response was received from the API.'
  }
  if (error instanceof ApiError) {
    return `The API responded with an error (HTTP ${error.status}).`
  }
  if (error instanceof UnexpectedResponseError) {
    return error.message
  }
  return 'An unexpected error occurred.'
}

/**
 * One plain-language sentence describing why a request failed, for display
 * after a caller-specific prefix such as "Samples could not be loaded.".
 */
export function describeApiError(error: unknown): string {
  if (error instanceof NetworkError) {
    return 'The API could not be reached. Check that the backend is running.'
  }
  if (error instanceof ApiError) {
    if (error.status >= 500) {
      return `The API returned an error (HTTP ${error.status}). Check that the backend is running and try again.`
    }
    return error.message
  }
  if (error instanceof UnexpectedResponseError) {
    return error.message
  }
  return 'An unexpected error occurred.'
}
