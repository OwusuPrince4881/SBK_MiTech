import { useEffect, useState } from 'react'

import { getSample } from '../api/samples'
import { describeApiError } from '../lib/errors'
import { formatUtcTimestamp } from '../lib/format'
import type { Sample } from '../types/sample'

type DetailState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; sample: Sample }

interface SampleDetailProps {
  sampleId: string | null
}

export function SampleDetail({ sampleId }: SampleDetailProps) {
  const [state, setState] = useState<DetailState>({ status: 'idle' })
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    if (sampleId === null) {
      setState({ status: 'idle' })
      return
    }

    const controller = new AbortController()
    setState({ status: 'loading' })
    getSample(sampleId, controller.signal).then(
      (sample) => {
        if (!controller.signal.aborted) {
          setState({ status: 'ready', sample })
        }
      },
      (error: unknown) => {
        if (!controller.signal.aborted) {
          setState({ status: 'error', message: describeApiError(error) })
        }
      },
    )
    return () => controller.abort()
  }, [sampleId, attempt])

  return (
    <section className="panel" aria-labelledby="detail-heading">
      <div className="panel__header">
        <div>
          <p className="eyebrow">Record</p>
          <h2 id="detail-heading" className="panel__title">
            Sample detail
          </h2>
        </div>
      </div>

      {state.status === 'idle' && (
        <div className="state state--empty">
          <p className="state__title">No sample selected</p>
          <p className="state__text">
            Select a sample from the registry to view its registration record.
          </p>
        </div>
      )}

      {state.status === 'loading' && (
        <div className="state" role="status">
          <div className="skeleton" aria-hidden="true" />
          <div className="skeleton" aria-hidden="true" />
          <p className="state__text">Loading sample record…</p>
        </div>
      )}

      {state.status === 'error' && (
        <div className="state state--error" role="alert">
          <p className="state__title">Sample record could not be loaded</p>
          <p className="state__text">{state.message}</p>
          <button
            type="button"
            className="btn btn--secondary"
            onClick={() => setAttempt((value) => value + 1)}
          >
            Try again
          </button>
        </div>
      )}

      {state.status === 'ready' && (
        <article className="record">
          <h3 className="record__title">{state.sample.sample_name}</h3>
          <dl className="record__fields">
            <div className="record__field">
              <dt>Sample ID</dt>
              <dd className="mono">{state.sample.id}</dd>
            </div>
            <div className="record__field">
              <dt>Collection location</dt>
              <dd>{state.sample.collection_location}</dd>
            </div>
            <div className="record__field record__field--wide">
              <dt>Material description</dt>
              <dd className="record__text">{state.sample.material_description}</dd>
            </div>
            <div className="record__field record__field--wide">
              <dt>Intended application</dt>
              <dd className="record__text">{state.sample.intended_application}</dd>
            </div>
            <div className="record__field">
              <dt>Registered (UTC)</dt>
              <dd>
                <time dateTime={state.sample.registered_at}>
                  {formatUtcTimestamp(state.sample.registered_at)}
                </time>
              </dd>
            </div>
          </dl>
          <p className="record__scope">
            This view shows registration details only. It does not display
            photographs, laboratory results or any assessment.
          </p>
        </article>
      )}
    </section>
  )
}
