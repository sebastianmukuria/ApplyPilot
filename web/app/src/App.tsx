import { useCallback, useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { api, type Job, type RunsResponse, type Stats } from './lib/api'
import { useAlertPrefs, useRunAlerts } from './lib/alerts'
import { DECK } from './lib/motion'
import { CommandBar, type View } from './components/CommandBar'
import { FlightLog } from './components/EasterEgg'
import { useFlightLog } from './lib/useFlightLog'
import { SettingsSheet } from './components/SettingsSheet'
import { Deck } from './views/Deck'
import { Queue } from './views/Queue'
import { Intel } from './views/Intel'
import { Answers } from './views/Answers'

export default function App() {
  const [view, setView] = useState<View>('deck')
  const [stats, setStats] = useState<Stats | null>(null)
  const [runs, setRuns] = useState<RunsResponse | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [model, setModel] = useState(() => localStorage.getItem('ap.model') ?? 'sonnet')
  const [refreshKey, setRefreshKey] = useState(0)
  const { tap, armed } = useFlightLog()
  const [alertPrefs, setAlertPrefs] = useAlertPrefs()
  useRunAlerts(runs, alertPrefs)

  const refresh = useCallback(() => {
    setRefreshKey((k) => k + 1)
    api.stats().then(setStats).catch(() => {})
    api.runs().then(setRuns).catch(() => {})
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  // poll run status (light) — 4s while anything is in flight, 15s idle
  const anyAlive = (runs?.runs ?? []).some((r) => r.alive)
  useEffect(() => {
    const ms = anyAlive ? 4000 : 15000
    const t = setInterval(() => api.runs().then(setRuns).catch(() => {}), ms)
    return () => clearInterval(t)
  }, [anyAlive])

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

      <CommandBar view={view} onView={setView} runs={runs} onLogoTap={tap} onSettings={() => setSettingsOpen(true)} />

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
              <Deck stats={stats} runs={runs} onGoQueue={() => setView('queue')} onChanged={refresh} onLaunch={launch} />
            )}
            {view === 'queue' && <Queue onLaunch={launch} refreshKey={refreshKey} />}
            {view === 'intel' && <Intel stats={stats} />}
            {view === 'answers' && <Answers />}
          </motion.div>
        </AnimatePresence>
      </main>

      <SettingsSheet
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        model={model}
        onModel={setModel}
        alertPrefs={alertPrefs}
        onAlertPrefs={setAlertPrefs}
      />
      <FlightLog trigger={armed} applied={(stats?.funnel.applied ?? 0) + (stats?.funnel.handoff ?? 0)} />
    </div>
  )
}
