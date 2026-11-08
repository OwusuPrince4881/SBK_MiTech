/**
 * Formats an API timestamp as `YYYY-MM-DD HH:mm:ss UTC`, independent of the
 * viewer's locale or time zone. Unparseable input is returned unchanged
 * rather than guessed at.
 */
export function formatUtcTimestamp(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) {
    return iso
  }
  return `${date.toISOString().slice(0, 19).replace('T', ' ')} UTC`
}

/** `YYYY-MM-DD` in UTC, or the input unchanged if it cannot be parsed. */
export function formatUtcDate(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toISOString().slice(0, 10)
}

/** First eight characters of an identifier, for compact display only. */
export function shortId(id: string): string {
  return id.slice(0, 8)
}

/** Registration time as a number for sorting; unparseable values sort oldest. */
export function registrationTime(iso: string): number {
  const time = new Date(iso).getTime()
  return Number.isNaN(time) ? 0 : time
}
