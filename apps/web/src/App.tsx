import { useCallback, useEffect, useRef, useState } from 'react'

import { listSamples } from './api/samples'
import { AppHeader } from './components/AppHeader'
import { EngineeringNotice } from './components/EngineeringNotice'
import { SampleDetail } from './components/SampleDetail'
import { SampleForm } from './components/SampleForm'
import { SampleList } from './components/SampleList'
import type { SampleListState } from './components/SampleList'
import { useBackendStatus } from './hooks/useBackendStatus'
import { describeApiError } from './lib/errors'
import type { Sample } from './types/sample'

export default function App() {
  const { status: backendStatus, recheck } = useBackendStatus()
  const [list, setList] = useState<SampleListState>({ status: 'loading' })
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const listRequest = useRef<AbortController | null>(null)
  const detailAnchor = useRef<HTMLDivElement>(null)

  const loadSamples = useCallback(async () => {
    listRequest.current?.abort()
    const controller = new AbortController()
    listRequest.current = controller
    // Keep showing the current list while refreshing; show the loading state
    // only when there is nothing to show yet.
    setList((current) =>
      current.status === 'ready'
        ? { ...current, refreshing: true }
        : { status: 'loading' },
    )
    try {
      const samples = await listSamples(controller.signal)
      if (!controller.signal.aborted) {
        setList({ status: 'ready', samples, refreshing: false })
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        setList({ status: 'error', message: describeApiError(error) })
      }
    }
  }, [])

  useEffect(() => {
    void loadSamples()
    return () => listRequest.current?.abort()
  }, [loadSamples])

  // On narrow screens the detail panel can sit below the fold; bring it into
  // view when a sample is selected. `nearest` is a no-op if it is already
  // visible.
  useEffect(() => {
    if (selectedId !== null) {
      detailAnchor.current?.scrollIntoView?.({ block: 'nearest' })
    }
  }, [selectedId])

  function handleRegistered(sample: Sample) {
    setSelectedId(sample.id)
    void loadSamples()
  }

  function handleReload() {
    void loadSamples()
    void recheck()
  }

  return (
    <div className="app">
      <AppHeader status={backendStatus} onRecheck={() => void recheck()} />
      <EngineeringNotice />
      <main className="workspace">
        <div className="page-title">
          <p className="eyebrow">Workspace</p>
          <h1>Sample registry</h1>
          <p className="page-title__lead">
            Register material samples and review their registration records.
          </p>
        </div>
        <div className="workspace__grid">
          <SampleList
            state={list}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onReload={handleReload}
          />
          <div className="workspace__side">
            <div ref={detailAnchor}>
              <SampleDetail sampleId={selectedId} />
            </div>
            <SampleForm onRegistered={handleRegistered} />
          </div>
        </div>
      </main>
    </div>
  )
}
