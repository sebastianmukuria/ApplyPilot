// The Flight Deck: hero, funnel band, live run, awaiting-confirmation.
import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import {
  ArrowSquareOut, BellRinging, Broom, Check, PaperPlaneTilt, Play, StopCircle,
} from '@phosphor-icons/react'
import { api, subscribeRunLog, type Job, type RunStatus, type Stats } from '../lib/api'
import { DECK, stagger } from '../lib/motion'
import { Bezel, CountUp, Eyebrow, GhostAction, IslandButton, ScoreChip, StatusPill } from '../components/ui'

const FUNNEL: { key: string; label: string }[] = [
  { key: 'total', label: 'Discovered' },
  { key: 'scored', label: 'Scored' },
  { key: 'ge7', label: 'Strong' },
  { key: 'tailored', label: 'Docs ready' },
  { key: 'applied', label: 'Applied' },
  { key: 'handoff', label: 'Handed off' },
  { key: 'failed', label: 'Failed' },
]

export function Deck({
  stats,
  run,
  onGoQueue,
  onChanged,
  onLaunch,
}: {
  stats: Stats | null
  run: RunStatus | null
  onGoQueue: () => void
  onChanged: () => void
  onLaunch: (j: Job) => void
}) {
  const [handoffs, setHandoffs] = useState<Job[]>([])

  useEffect(() => {
    api.handoffs().then((r) => setHandoffs(Array.isArray(r) ? r : r.jobs)).catch(() => {})
  }, [run?.active, stats?.funnel.handoff])

  const funnel = stats?.funnel ?? {}

  return (
    <motion.div
      variants={stagger}
      initial="hidden"
      animate="show"
      className="mx-auto w-full max-w-7xl px-4 pb-32"
    >
      {/* hero row */}
      <div className="grid gap-4 lg:grid-cols-12">
        <Bezel className="lg:col-span-8" i={0}>
          <div className="relative overflow-hidden p-8 sm:p-10">
            <div className="pointer-events-none absolute -right-20 -top-24 h-72 w-72 rounded-full bg-clay/[0.07] blur-3xl" />
            <Eyebrow>Application campaign</Eyebrow>
            <h1 className="mt-5 font-display text-[clamp(2.4rem,5vw,3.6rem)] font-semibold leading-[1.02] tracking-tight text-ink">
              Flight deck<span className="text-clay">.</span>
            </h1>
            <p className="mt-3 max-w-md text-[14px] leading-relaxed text-mut">
              {funnel.total?.toLocaleString() ?? '—'} roles discovered, {funnel.tailored ?? '—'} with
              documents ready. The autopilot fills, you approve.
            </p>
            <div className="mt-7 flex flex-wrap gap-3">
              <IslandButton onClick={onGoQueue} icon={<Play weight="fill" size={12} />}>
                Open the queue
              </IslandButton>
            </div>
          </div>
        </Bezel>

        <Bezel className="lg:col-span-4" i={1}>
          <div className="flex h-full flex-col justify-between p-8">
            <Eyebrow>Est. LLM spend</Eyebrow>
            <div>
              <div className="font-display text-5xl font-semibold tracking-tight text-ink">
                $<CountUp value={stats?.spend.cost ?? 0} format={(n) => n.toFixed(2)} />
              </div>
              <div className="tnum mt-2 text-[12px] text-faint">
                ${stats?.spend.today.toFixed(2) ?? '0.00'} today · {stats?.spend.calls.toLocaleString() ?? 0} calls
              </div>
              <div className="mt-1 text-[11px] text-faint">Apply runs ride the Claude subscription — $0.</div>
            </div>
          </div>
        </Bezel>
      </div>

      {/* funnel band */}
      <Bezel className="mt-4" i={2}>
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7">
          {FUNNEL.map((f, i) => (
            <div
              key={f.key}
              className={`p-5 ${i > 0 ? 'border-l border-white/[0.06]' : ''} ${i >= 4 ? 'max-lg:border-t max-lg:border-white/[0.06]' : ''}`}
            >
              <div className="text-[10px] font-medium uppercase tracking-[0.18em] text-faint">{f.label}</div>
              <div
                className={`mt-1.5 font-display text-[28px] font-semibold tracking-tight ${
                  f.key === 'failed' && (funnel[f.key] ?? 0) > 0 ? 'text-clay-hi' : 'text-ink'
                }`}
              >
                <CountUp value={funnel[f.key] ?? 0} />
              </div>
            </div>
          ))}
        </div>
      </Bezel>

      {/* live run + handoffs */}
      <div className="mt-4 grid gap-4 lg:grid-cols-12">
        <Bezel className="lg:col-span-7" i={3}>
          <LiveRun run={run} onChanged={onChanged} />
        </Bezel>

        <Bezel className="lg:col-span-5" i={4}>
          <div className="flex h-full flex-col p-6">
            <div className="mb-4 flex items-center justify-between">
              <Eyebrow>Awaiting your confirmation</Eyebrow>
              <span className="tnum text-[12px] text-faint">{handoffs.length}</span>
            </div>
            {handoffs.length === 0 ? (
              <div className="flex flex-1 items-center justify-center py-10 text-[13px] italic text-faint">
                Nothing waiting on you. Clear skies.
              </div>
            ) : (
              <div className="space-y-2.5 overflow-auto">
                {handoffs.map((j) => (
                  <div
                    key={j.url}
                    className="flex items-center gap-3 rounded-2xl bg-white/[0.025] p-3 ring-1 ring-white/[0.06]"
                  >
                    <ScoreChip score={j.fit_score} />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[13px] font-medium text-ink">{j.company}</div>
                      <div className="truncate text-[11.5px] text-faint">{j.title}</div>
                    </div>
                    <GhostAction href={j.application_url ?? j.url} title="Open the application">
                      <ArrowSquareOut weight="light" size={13} />
                    </GhostAction>
                    <GhostAction
                      onClick={async () => {
                        await api.markApplied(j.url)
                        setHandoffs((h) => h.filter((x) => x.url !== j.url))
                        onChanged()
                      }}
                      title="I submitted it"
                    >
                      <Check weight="light" size={13} /> Done
                    </GhostAction>
                    <GhostAction onClick={() => onLaunch(j)} title="Re-run the apply">
                      <Play weight="light" size={13} />
                    </GhostAction>
                  </div>
                ))}
              </div>
            )}
          </div>
        </Bezel>
      </div>
    </motion.div>
  )
}

function LiveRun({ run, onChanged }: { run: RunStatus | null; onChanged: () => void }) {
  const [lines, setLines] = useState<string[]>([])
  const wellRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!run?.active) {
      setLines([])
      return
    }
    const off = subscribeRunLog({
      onLog: (l) => setLines((prev) => [...prev.slice(-400), l]),
      onEnd: onChanged,
    })
    return off
    // eslint-disable-next-line react-hooks/exhaustive-deps -- onChanged is a stable useCallback; re-subscribing per identity churn would drop the stream
  }, [run?.active, run?.url])

  useEffect(() => {
    wellRef.current?.scrollTo({ top: wellRef.current.scrollHeight })
  }, [lines])

  return (
    <div className="flex h-full min-h-[320px] flex-col p-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <Eyebrow>Live run</Eyebrow>
        {run?.active && (
          <div className="flex items-center gap-2">
            {run.done && run.status && <StatusPill status={run.status} />}
            {run.done && (
              <GhostAction
                onClick={async () => {
                  if (run.url) await api.markApplied(run.url)
                  await api.clearRun()
                  onChanged()
                }}
              >
                <Check weight="light" size={13} /> Mark applied
              </GhostAction>
            )}
            <GhostAction
              onClick={async () => {
                await api.stopRun()
                onChanged()
              }}
              title="Kill the run, the agent, and its Chrome"
            >
              <StopCircle weight="light" size={13} /> Stop
            </GhostAction>
            <GhostAction
              onClick={async () => {
                await api.clearRun()
                onChanged()
              }}
            >
              <Broom weight="light" size={13} /> Clear
            </GhostAction>
          </div>
        )}
      </div>

      <AnimatePresence mode="wait">
        {run?.active ? (
          <motion.div
            key="active"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="flex min-h-0 flex-1 flex-col"
          >
            {run.needs_you ? (
              <motion.div
                initial={{ scale: 0.98 }}
                animate={{ scale: 1 }}
                transition={{ duration: 0.5, ease: DECK }}
                className="mb-3 flex items-center gap-3 rounded-2xl bg-clay/10 px-4 py-3 ring-1 ring-clay/30"
              >
                <BellRinging weight="light" size={18} className="animate-pulse text-clay-hi" />
                <span className="text-[13px] text-clay-hi">
                  <strong className="font-display tracking-wide">{run.company}</strong> needs you — solve the
                  CAPTCHA or review &amp; submit in the open Chrome window.
                </span>
              </motion.div>
            ) : (
              <div className="mb-3 text-[13px] text-mut">
                {run.done ? 'Run finished — ' : 'Working on '}
                <span className="font-medium text-ink">{run.company}</span>
                {!run.done && <span className="text-faint"> · {run.model}</span>}
              </div>
            )}
            <div ref={wellRef} className="logwell min-h-0 flex-1 overflow-auto rounded-2xl bg-white/[0.02] p-4 ring-1 ring-white/[0.05]">
              {lines.length ? lines.join('\n') : '(connecting to the run…)'}
            </div>
          </motion.div>
        ) : (
          <motion.div
            key="idle"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="flex flex-1 flex-col items-center justify-center gap-3 py-10 text-center"
          >
            <PaperPlaneTilt weight="thin" size={42} className="text-faint" />
            <div className="text-[13px] italic text-faint">
              No run in flight — hit Apply on any card in the queue.
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
