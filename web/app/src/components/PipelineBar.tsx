// Pipeline controls on the Deck: scan for new jobs + autopilot batch applies.
// Feature-detects the backend; renders nothing until the endpoints exist.
import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { CircleNotch, MagnifyingGlass, PaperPlaneTilt, StopCircle } from '@phosphor-icons/react'
import { DECK } from '../lib/motion'

interface PipelineState {
  running: boolean
  current_stage?: string | null
  done?: Record<string, unknown>
  error?: string | null
  done_at?: string | null
}
interface AutopilotState {
  running: boolean
  target?: number
  launched?: number
  finished?: number
  stopped?: boolean
}

async function getJson<T>(url: string): Promise<T | null> {
  try {
    const r = await fetch(url)
    if (!r.ok) return null
    return (await r.json()) as T
  } catch {
    return null
  }
}

export function PipelineBar({ onChanged }: { onChanged: () => void }) {
  const [pipe, setPipe] = useState<PipelineState | null>(null)
  const [auto, setAuto] = useState<AutopilotState | null>(null)
  const [available, setAvailable] = useState(false)
  const [count, setCount] = useState(5)
  const [err, setErr] = useState('')

  const load = async () => {
    const p = await getJson<PipelineState>('/api/pipeline/status')
    const a = await getJson<AutopilotState>('/api/autopilot/status')
    if (p) setAvailable(true)
    setPipe(p)
    setAuto(a)
  }

  useEffect(() => {
    load()
  }, [])

  // poll while anything runs
  const busy = !!(pipe?.running || auto?.running)
  useEffect(() => {
    if (!busy) return
    const t = setInterval(async () => {
      await load()
      onChanged()
    }, 5000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busy])

  if (!available) return null

  const post = async (url: string, body?: unknown) => {
    setErr('')
    try {
      const r = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined,
      })
      if (!r.ok) throw new Error((await r.text()).slice(0, 140))
      await load()
    } catch (e) {
      setErr(String(e))
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.6, ease: DECK }}
      className="mt-4 flex flex-wrap items-center gap-3 rounded-full bg-white/[0.025] px-4 py-2.5 ring-1 ring-white/[0.07]"
    >
      {/* scan */}
      {pipe?.running ? (
        <span className="flex items-center gap-2 text-[12.5px] text-sky">
          <CircleNotch size={13} className="animate-spin" />
          Scanning — {pipe.current_stage ?? 'starting'}…
        </span>
      ) : (
        <button
          onClick={() => post('/api/pipeline/run', { stages: ['all'] })}
          className="flex items-center gap-2 rounded-full px-3.5 py-1.5 text-[12.5px] text-mut ring-1 ring-white/[0.08] transition-all hover:text-ink hover:ring-white/20"
        >
          <MagnifyingGlass weight="light" size={13} /> Scan for new jobs
        </button>
      )}

      <span className="h-4 w-px bg-white/10" />

      {/* autopilot */}
      {auto?.running ? (
        <span className="flex items-center gap-2.5 text-[12.5px] text-sky">
          <PaperPlaneTilt weight="light" size={13} className="animate-pulse" />
          Autopilot {auto.finished ?? 0}/{auto.target}
          <button
            onClick={() => post('/api/autopilot/stop')}
            className="flex items-center gap-1 rounded-full px-2.5 py-1 text-[11.5px] text-mut ring-1 ring-white/[0.08] transition-all hover:text-clay-hi"
          >
            <StopCircle weight="light" size={12} /> Stop
          </button>
        </span>
      ) : (
        <span className="flex items-center gap-2">
          <button
            onClick={() => post('/api/autopilot', { count })}
            className="flex items-center gap-2 rounded-full px-3.5 py-1.5 text-[12.5px] text-mut ring-1 ring-white/[0.08] transition-all hover:text-ink hover:ring-white/20"
            title="Queue applies for the best unapplied jobs, using all run slots"
          >
            <PaperPlaneTilt weight="light" size={13} /> Autopilot top
          </button>
          <select
            value={count}
            onChange={(e) => setCount(Number(e.target.value))}
            className="rounded-full bg-transparent px-2 py-1.5 text-[12.5px] text-mut ring-1 ring-white/[0.07] outline-none"
          >
            {[3, 5, 10, 20].map((n) => (
              <option key={n} value={n} className="bg-[#111]">{n}</option>
            ))}
          </select>
        </span>
      )}

      <AnimatePresence>
        {pipe?.error && (
          <motion.span initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="text-[11.5px] text-clay-hi">
            Last scan hit an error — check the terminal log.
          </motion.span>
        )}
        {err && (
          <motion.span initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="truncate text-[11.5px] text-clay-hi">
            {err}
          </motion.span>
        )}
      </AnimatePresence>
    </motion.div>
  )
}
