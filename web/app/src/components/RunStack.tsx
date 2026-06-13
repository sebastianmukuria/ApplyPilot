// Concurrent run cards: one mini terminal per active run, slot indicator.
import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { BellRinging, Broom, Check, PaperPlaneTilt, StopCircle } from '@phosphor-icons/react'
import { api, subscribeRunLog, type RunStatus, type RunsResponse } from '../lib/api'
import { DECK } from '../lib/motion'
import { Eyebrow, GhostAction, StatusPill } from './ui'

export function RunStack({ runs, onChanged }: { runs: RunsResponse | null; onChanged: () => void }) {
  const list = runs?.runs ?? []
  return (
    <div className="flex h-full min-h-[320px] flex-col p-6">
      <div className="mb-4 flex items-center justify-between">
        <Eyebrow>Live runs</Eyebrow>
        {runs && (
          <span className="tnum text-[12px] text-faint">
            {runs.slots - runs.free}/{runs.slots} slots
            {runs.free === 0 && <span className="text-clay-hi"> · full</span>}
          </span>
        )}
      </div>

      {list.length === 0 ? (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="flex flex-1 flex-col items-center justify-center gap-3 py-10 text-center"
        >
          <PaperPlaneTilt weight="thin" size={42} className="text-faint" />
          <div className="text-[13px] italic text-faint">
            No runs in flight — hit Apply on cards in the queue (up to {runs?.slots ?? 3} at once).
          </div>
        </motion.div>
      ) : (
        <div className="min-h-0 flex-1 space-y-3 overflow-auto">
          <AnimatePresence>
            {list.map((r) => (
              <RunCard key={r.run_id} run={r} onChanged={onChanged} />
            ))}
          </AnimatePresence>
        </div>
      )}
    </div>
  )
}

function RunCard({ run, onChanged }: { run: RunStatus; onChanged: () => void }) {
  const [lines, setLines] = useState<string[]>([])
  const [open, setOpen] = useState(true)
  const wellRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!run.run_id || !run.alive) return
    const off = subscribeRunLog(run.run_id, {
      onLog: (l) => setLines((prev) => [...prev.slice(-300), l]),
      onEnd: onChanged,
    })
    return off
    // eslint-disable-next-line react-hooks/exhaustive-deps -- stable callback; resubscribing drops the stream
  }, [run.run_id, run.alive])

  useEffect(() => {
    wellRef.current?.scrollTo({ top: wellRef.current.scrollHeight })
  }, [lines, open])

  const led = run.needs_you
    ? 'bg-clay animate-pulse shadow-[0_0_10px_2px_rgba(217,119,87,0.5)]'
    : run.done
      ? 'bg-sage'
      : run.alive
        ? 'bg-sky animate-pulse'
        : 'bg-white/20'

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.98 }}
      transition={{ duration: 0.4, ease: DECK }}
      className="rounded-2xl bg-white/[0.02] ring-1 ring-white/[0.06]"
    >
      <div className="flex flex-wrap items-center gap-2.5 px-4 py-3">
        <span className={`h-2 w-2 flex-none rounded-full ${led}`} />
        <button
          onClick={() => setOpen((o) => !o)}
          className="min-w-0 flex-1 truncate text-left text-[13px] font-medium text-ink"
          title={run.url}
        >
          {run.company ?? '?'}
          <span className="ml-2 text-[11px] font-normal text-faint">
            slot {run.worker_slot} · {run.model}
          </span>
        </button>
        {run.done && run.status && <StatusPill status={run.status} />}
        {run.done && run.url && (
          <GhostAction
            onClick={async () => {
              await api.markApplied(run.url!)
              if (run.run_id) await api.clearRun(run.run_id)
              onChanged()
            }}
          >
            <Check weight="light" size={13} /> Applied
          </GhostAction>
        )}
        {run.alive ? (
          <GhostAction
            onClick={async () => {
              if (run.run_id) await api.stopRun(run.run_id)
              onChanged()
            }}
            title="Stop this run only"
          >
            <StopCircle weight="light" size={13} /> Stop
          </GhostAction>
        ) : (
          <GhostAction
            onClick={async () => {
              if (run.run_id) await api.clearRun(run.run_id)
              onChanged()
            }}
          >
            <Broom weight="light" size={13} /> Clear
          </GhostAction>
        )}
      </div>

      {run.needs_you && (
        <div className="mx-4 mb-3 flex items-center gap-2.5 rounded-xl bg-clay/10 px-3.5 py-2.5 ring-1 ring-clay/25">
          <BellRinging weight="light" size={15} className="animate-pulse text-clay-hi" />
          <span className="text-[12px] text-clay-hi">
            Needs you — CAPTCHA or final review in this run's Chrome window.
          </span>
        </div>
      )}

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.35, ease: DECK }}
            className="overflow-hidden"
          >
            <div
              ref={wellRef}
              className="logwell mx-4 mb-4 max-h-44 overflow-auto rounded-xl bg-black/30 p-3 ring-1 ring-white/[0.04]"
            >
              {lines.length ? lines.join('\n') : run.alive ? '(connecting…)' : '(run ended)'}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  )
}
