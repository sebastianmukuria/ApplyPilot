// Pipeline radar: what happened AFTER applying — funnel, response rates, events.
import { useEffect, useState } from 'react'
import { motion } from 'motion/react'
import { EnvelopeOpen } from '@phosphor-icons/react'
import { api, type AppEvent, type OutcomesSummary } from '../lib/api'
import { Bezel, Eyebrow } from './ui'

const STAGES: { key: string; label: string; color: string }[] = [
  { key: 'applied', label: 'Applied', color: 'bg-white/25' },
  { key: 'responded', label: 'Responded', color: 'bg-sky/70' },
  { key: 'screen', label: 'Screen', color: 'bg-sky' },
  { key: 'interview', label: 'Interview', color: 'bg-clay/80' },
  { key: 'offer', label: 'Offer', color: 'bg-sage' },
]

const EVENT_LABEL: Record<string, string> = {
  applied: 'applied', screen_invite: 'screen invite', interview_scheduled: 'interview',
  reschedule: 'reschedule', rejection: 'rejection', offer: 'offer',
  recruiter_inbound: 'recruiter reached out', responded: 'responded',
  screen: 'screen', interview: 'interview', rejected: 'rejection',
}

export function OutcomesSection() {
  const [summary, setSummary] = useState<OutcomesSummary | null>(null)
  const [events, setEvents] = useState<AppEvent[]>([])
  const [missing, setMissing] = useState(false)

  useEffect(() => {
    api.outcomesSummary().then(setSummary).catch(() => setMissing(true))
    api.outcomesEvents().then((r) => setEvents(Array.isArray(r) ? r : r.events)).catch(() => {})
  }, [])

  if (missing) return null // backend not landed yet — section hides itself

  const funnel = summary?.funnel ?? {}
  const applied = funnel.applied ?? 0
  const rejected = funnel.rejected ?? 0

  return (
    <>
      <Bezel className="lg:col-span-7" i={4}>
        <div className="p-6">
          <div className="flex items-baseline justify-between">
            <Eyebrow>Pipeline radar</Eyebrow>
            {rejected > 0 && (
              <span className="tnum text-[11.5px] text-faint">{rejected} rejection{rejected === 1 ? '' : 's'}</span>
            )}
          </div>
          <div className="mt-6 space-y-3.5">
            {STAGES.map((s) => {
              const v = funnel[s.key] ?? 0
              const pct = applied > 0 ? (v / applied) * 100 : 0
              return (
                <div key={s.key}>
                  <div className="mb-1 flex items-baseline justify-between text-[12px]">
                    <span className="text-mut">{s.label}</span>
                    <span className="tnum text-faint">
                      {v.toLocaleString()}
                      {s.key !== 'applied' && applied > 0 && (
                        <span className="ml-1.5 text-[10.5px]">{pct.toFixed(0)}%</span>
                      )}
                    </span>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full bg-white/[0.04]">
                    <motion.div
                      initial={{ scaleX: 0 }}
                      whileInView={{ scaleX: 1 }}
                      viewport={{ once: true }}
                      transition={{ duration: 0.9, ease: [0.32, 0.72, 0, 1] }}
                      style={{ width: `${Math.max(pct, v > 0 ? 2 : 0)}%`, transformOrigin: 'left' }}
                      className={`h-full rounded-full ${s.color}`}
                    />
                  </div>
                </div>
              )
            })}
          </div>
          {(summary?.by_score?.length || summary?.by_source?.length) ? (
            <div className="mt-6 grid gap-4 border-t border-white/[0.06] pt-4 sm:grid-cols-2">
              <RateTable title="Response rate by score" rows={(summary?.by_score ?? []).map((r) => ({
                label: `score ${r.band}`, applied: r.applied, rate: r.response_rate }))} />
              <RateTable title="By source" rows={(summary?.by_source ?? []).map((r) => ({
                label: r.site, applied: r.applied, rate: r.response_rate }))} />
            </div>
          ) : null}
        </div>
      </Bezel>

      <Bezel className="lg:col-span-5" i={5}>
        <div className="flex h-full flex-col p-6">
          <Eyebrow>Responses &amp; interviews</Eyebrow>
          {events.length === 0 ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-3 py-10 text-center">
              <EnvelopeOpen weight="thin" size={36} className="text-faint" />
              <p className="max-w-64 text-[12.5px] italic leading-relaxed text-faint">
                Real replies land here — interview invites, rejections, recruiter
                outreach. Application receipts are filtered out. Connect Gmail in
                Settings to populate it automatically.
              </p>
            </div>
          ) : (
            <div className="mt-4 min-h-0 flex-1 space-y-2 overflow-auto">
              {events.map((e) => (
                <div key={e.id} className="rounded-xl bg-white/[0.02] px-3.5 py-2.5 ring-1 ring-white/[0.05]">
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="truncate text-[12.5px] font-medium text-ink">
                      {e.job_company ?? e.company}
                    </span>
                    <span className={`flex-none text-[10.5px] uppercase tracking-[0.14em] ${
                      e.event_type === 'rejection' || e.event_type === 'rejected'
                        ? 'text-clay-hi'
                        : e.event_type === 'offer'
                          ? 'text-sage'
                          : 'text-sky'
                    }`}>
                      {EVENT_LABEL[e.event_type] ?? e.event_type}
                    </span>
                  </div>
                  <div className="mt-0.5 truncate text-[11px] text-faint">
                    {e.subject ?? e.role ?? ''}
                    {e.email_ts && <span className="ml-1.5">· {e.email_ts.slice(0, 10)}</span>}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </Bezel>
    </>
  )
}

function RateTable({ title, rows }: { title: string; rows: { label: string; applied: number; rate: number }[] }) {
  if (!rows.length) return null
  return (
    <div>
      <div className="mb-2 text-[10.5px] uppercase tracking-[0.16em] text-faint">{title}</div>
      <div className="space-y-1.5">
        {rows.map((r) => (
          <div key={r.label} className="flex items-baseline justify-between text-[12px]">
            <span className="truncate pr-2 text-mut">{r.label}</span>
            <span className="tnum flex-none text-faint">
              {(r.rate * 100).toFixed(0)}% <span className="text-[10.5px]">of {r.applied}</span>
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
