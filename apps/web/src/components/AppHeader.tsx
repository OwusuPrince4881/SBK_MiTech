import type { BackendStatus } from '../hooks/useBackendStatus'

interface AppHeaderProps {
  status: BackendStatus
  onRecheck: () => void
}

export function AppHeader({ status, onRecheck }: AppHeaderProps) {
  return (
    <header className="app-header">
      <div className="app-header__inner">
        <div className="brand">
          <svg
            className="brand__mark"
            viewBox="0 0 32 32"
            width="32"
            height="32"
            aria-hidden="true"
            focusable="false"
          >
            <rect x="2" y="5" width="28" height="6" fill="#c98a1b" />
            <rect x="2" y="13" width="19" height="6" fill="#e8e2d6" />
            <rect x="2" y="21" width="24" height="6" fill="#8a8174" />
          </svg>
          <div>
            <p className="brand__name">MineMat AI</p>
            <p className="brand__tagline">Mine construction materials</p>
          </div>
        </div>
        <ConnectionIndicator status={status} onRecheck={onRecheck} />
      </div>
    </header>
  )
}

function ConnectionIndicator({ status, onRecheck }: AppHeaderProps) {
  return (
    <div
      className={`connection connection--${status.state}`}
      role="status"
      aria-live="polite"
    >
      <span className="connection__dot" aria-hidden="true" />
      {status.state === 'checking' && <span>Checking API…</span>}
      {status.state === 'connected' && (
        <>
          <span>API connected</span>
          <span className="connection__meta">v{status.version}</span>
        </>
      )}
      {status.state === 'unavailable' && (
        <>
          <span>API unavailable</span>
          <button type="button" className="connection__retry" onClick={onRecheck}>
            Check again
          </button>
        </>
      )}
    </div>
  )
}
