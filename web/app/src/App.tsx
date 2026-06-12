import { useCallback, useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { api, type Job, type RunStatus, type Stats } from './lib/api'
import { DECK } from './lib/motion'
import { CommandBar, type View } from './components/CommandBar'
import { FlightLog, useFlightLog } from './components/EasterEgg'
import { SettingsSheet } from './components/SettingsSheet'
import { Deck } from './views/Deck'
import { Queue } from './views/Queue'
import { Intel } from './views/Intel'
import { Answers } from './views/Answers'

export default function App() {
  const [view, setView] = useState<View>('deck')
  const [stats, setStats] = useState<Stats | null>(null)
  const [run, setRun] = useState<RunStatus | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [model, setModel] = useState(() => localStorage.getItem('ap.model') ?? 'sonnet')
  const [refreshKey, setRefreshKey] = useState(0)
  const { tap, armed } = useFlightLog()

  const refresh = useCallback(() => {
    setRefreshKey((k) => k + 1)
    api.stats().then(setStats).catch(() => {})
    api.run().then(setRun).catch(() => {})
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  // poll the run status (light) — 4s while active, 15s idle
  useEffect(() => {
    const ms = run?.active ? 4000 : 15000
    const t = setInterval(() => api.run().then(setRun).catch(() => {}), ms)
    return () => clearInterval(t)
  }, [run?.active])

  useEffect(() => {
    localStorage.setItem('ap.model', model)
  }, [model])

  const launch = useCallback(
    async (job: Job) => {
      try {
        await api.startRun(job.url, model)
        setView('deck')
        refresh()
      } catch (e) {
        alert(String(e))
      }
    },
    [model, refresh],
  )

  return (
    <div className="relative min-h-[100dvh]">
      <div className="aurora" />
      <div className="grain" />

      <CommandBar view={view} onView={setView} run={run} onLogoTap={tap} onSettings={() => setSettingsOpen(true)} />

      <main className="relative z-10 pt-28">
        <AnimatePresence mode="wait">
          <motion.div
            key={view}
            initial={{ opacity: 0, y: 14, filter: 'blur(4px)' }}
            animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
            exit={{ opacity: 0, y: -10, filter: 'blur(4px)', transition: { duration: 0.22 } }}
            transition={{ duration: 0.55, ease: DECK }}
          >
            {view === 'deck' && (
              <Deck stats={stats} run={run} onGoQueue={() => setView('queue')} onChanged={refresh} onLaunch={launch} />
            )}
            {view === 'queue' && <Queue onLaunch={launch} refreshKey={refreshKey} />}
            {view === 'intel' && <Intel stats={stats} />}
            {view === 'answers' && <Answers />}
          </motion.div>
        </AnimatePresence>
      </main>

      <SettingsSheet open={settingsOpen} onClose={() => setSettingsOpen(false)} model={model} onModel={setModel} />
      <FlightLog trigger={armed} applied={(stats?.funnel.applied ?? 0) + (stats?.funnel.handoff ?? 0)} />
    </div>
  )
}
