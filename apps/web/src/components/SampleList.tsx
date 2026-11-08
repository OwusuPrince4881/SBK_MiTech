import { formatUtcDate, registrationTime, shortId } from '../lib/format'
import type { Sample } from '../types/sample'

export type SampleListState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; samples: Sample[]; refreshing: boolean }

interface SampleListProps {
  state: SampleListState
  selectedId: string | null
  onSelect: (sampleId: string) => void
  onReload: () => void
}

function newestFirst(samples: Sample[]): Sample[] {
  return [...samples].sort(
    (a, b) => registrationTime(b.registered_at) - registrationTime(a.registered_at),
  )
}

export function SampleList({
  state,
  selectedId,
  onSelect,
  onReload,
}: SampleListProps) {
  const busy =
    state.status === 'loading' ||
    (state.status === 'ready' && state.refreshing)

  return (
    <section className="panel" aria-labelledby="registry-heading">
      <div className="panel__header">
        <div>
          <p className="eyebrow">Registry</p>
          <h2 id="registry-heading" className="panel__title">
            Registered samples
          </h2>
        </div>
        <div className="panel__actions">
          {state.status === 'ready' && (
            <span className="badge">
              {state.samples.length}{' '}
              {state.samples.length === 1 ? 'sample' : 'samples'}
            </span>
          )}
          <button
            type="button"
            className="btn btn--quiet"
            onClick={onReload}
            disabled={busy}
          >
            Refresh
          </button>
        </div>
      </div>

      {state.status === 'loading' && (
        <div className="state" role="status">
          <div className="skeleton" aria-hidden="true" />
          <div className="skeleton" aria-hidden="true" />
          <div className="skeleton" aria-hidden="true" />
          <p className="state__text">Loading samples…</p>
        </div>
      )}

      {state.status === 'error' && (
        <div className="state state--error" role="alert">
          <p className="state__title">Samples could not be loaded</p>
          <p className="state__text">{state.message}</p>
          <button type="button" className="btn btn--secondary" onClick={onReload}>
            Try again
          </button>
        </div>
      )}

      {state.status === 'ready' && state.samples.length === 0 && (
        <div className="state state--empty">
          <p className="state__title">No samples registered yet</p>
          <p className="state__text">
            Use the registration form to add the first sample. Registered
            samples will be listed here.
          </p>
        </div>
      )}

      {state.status === 'ready' && state.samples.length > 0 && (
        <>
          <p className="list-note">Newest first</p>
          <ul className="sample-list" aria-busy={state.refreshing}>
            {newestFirst(state.samples).map((sample) => {
              const selected = sample.id === selectedId
              return (
                <li key={sample.id}>
                  <button
                    type="button"
                    className={`sample-row${selected ? ' sample-row--selected' : ''}`}
                    aria-current={selected ? 'true' : undefined}
                    onClick={() => onSelect(sample.id)}
                  >
                    <span className="sample-row__name">{sample.sample_name}</span>
                    <span className="sample-row__location">
                      {sample.collection_location}
                    </span>
                    <span className="sample-row__meta">
                      <span className="mono">ID {shortId(sample.id)}</span>
                      <span>Registered {formatUtcDate(sample.registered_at)}</span>
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
        </>
      )}
    </section>
  )
}
