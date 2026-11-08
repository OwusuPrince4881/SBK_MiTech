import { useCallback, useEffect, useRef, useState } from 'react'

import { getBackendHealth } from '../api/samples'

export type BackendStatus =
  | { state: 'checking' }
  | { state: 'connected'; version: string }
  | { state: 'unavailable' }

const DEFAULT_POLL_INTERVAL_MS = 30_000

/**
 * Tracks whether the backend answers `GET /health`: checked on mount, on a
 * timer, and on demand through `recheck`. `/health` does not test the
 * database, so "connected" means the API process is responding.
 */
export function useBackendStatus(pollIntervalMs = DEFAULT_POLL_INTERVAL_MS): {
  status: BackendStatus
  recheck: () => Promise<void>
} {
  const [status, setStatus] = useState<BackendStatus>({ state: 'checking' })
  const controllerRef = useRef<AbortController | null>(null)

  const recheck = useCallback(async () => {
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    try {
      const health = await getBackendHealth(controller.signal)
      if (!controller.signal.aborted) {
        setStatus(
          health.status === 'healthy'
            ? { state: 'connected', version: health.version }
            : { state: 'unavailable' },
        )
      }
    } catch {
      if (!controller.signal.aborted) {
        setStatus({ state: 'unavailable' })
      }
    }
  }, [])

  useEffect(() => {
    void recheck()
    const timer = window.setInterval(() => void recheck(), pollIntervalMs)
    return () => {
      window.clearInterval(timer)
      controllerRef.current?.abort()
    }
  }, [recheck, pollIntervalMs])

  return { status, recheck }
}
