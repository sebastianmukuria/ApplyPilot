// Intel: animated charts over the campaign data.
import { useMemo } from 'react'
import { motion } from 'motion/react'
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import type { Stats } from '../lib/api'
import { stagger } from '../lib/motion'
import { OutcomesSection } from '../components/Outcomes'
import { Bezel, Eyebrow } from '../components/ui'

const CLAY = '#d97757'
const GRID = 'rgba(255,255,255,0.05)'
const TICK = { fill: '#8a8a85', fontSize: 11, fontFamily: 'Geist Variable' }

function HudTooltip({ active, payload, label }: { active?: boolean; payload?: { value: number }[]; label?: string }) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-xl bg-black/80 px-3 py-2 text-[12px] ring-1 ring-white/15 backdrop-blur-xl">
      <div className="text-faint">{label}</div>
      <div className="tnum font-medium text-clay-hi">{payload[0].value}</div>
    </div>
  )
}

export function Intel({ stats }: { stats: Stats | null }) {
  const perDay = stats?.per_day ?? []
  const byStatus = stats?.status_breakdown ?? []
  const byScore = stats?.score_distribution ?? []
  const AREA_LABEL: Record<string, string> = {
    apply: 'Applying (agent)', pipeline: 'Scoring & documents', other: 'Other',
  }
  const spendAreas = useMemo(
    () => Object.entries(stats?.spend.sub_by_area ?? {}).sort((a, b) => b[1].cost - a[1].cost),
    [stats],
  )
  const billable = stats?.spend.cost ?? 0

  return (
    <motion.div variants={stagger} initial="hidden" animate="show" className="mx-auto w-full max-w-7xl px-4 pb-32">
      <div className="grid gap-4 lg:grid-cols-12">
        <Bezel className="lg:col-span-8" i={0}>
          <div className="p-6">
            <Eyebrow>Applications per day</Eyebrow>
            <div className="mt-5 h-64">
              {perDay.length ? (
                <ResponsiveContainer>
                  <AreaChart data={perDay} margin={{ left: -22, right: 8, top: 4 }}>
                    <defs>
                      <linearGradient id="clayfill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor={CLAY} stopOpacity={0.35} />
                        <stop offset="100%" stopColor={CLAY} stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid stroke={GRID} vertical={false} />
                    <XAxis dataKey="date" tick={TICK} axisLine={false} tickLine={false} />
                    <YAxis tick={TICK} axisLine={false} tickLine={false} allowDecimals={false} />
                    <Tooltip content={<HudTooltip />} cursor={{ stroke: 'rgba(255,255,255,0.15)' }} />
                    <Area
                      type="monotone"
                      dataKey="applications"
                      stroke={CLAY}
                      strokeWidth={2}
                      fill="url(#clayfill)"
                      animationDuration={1200}
                      style={{ filter: `drop-shadow(0 0 6px ${CLAY}66)` }}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              ) : (
                <Empty>No applications yet — the chart draws itself as you fly.</Empty>
              )}
            </div>
          </div>
        </Bezel>

        <Bezel className="lg:col-span-4" i={1}>
          <div className="p-6">
            <Eyebrow>Pipeline status</Eyebrow>
            <div className="mt-5 space-y-3">
              {byStatus.length ? (
                byStatus.map((s) => {
                  const max = Math.max(...byStatus.map((x) => x.count))
                  return (
                    <div key={s.status}>
                      <div className="mb-1 flex items-baseline justify-between text-[12px]">
                        <span className="text-mut">{s.status}</span>
                        <span className="tnum text-faint">{s.count.toLocaleString()}</span>
                      </div>
                      <div className="h-1.5 overflow-hidden rounded-full bg-white/[0.05]">
                        <motion.div
                          initial={{ scaleX: 0 }}
                          whileInView={{ scaleX: 1 }}
                          viewport={{ once: true }}
                          transition={{ duration: 1, ease: [0.32, 0.72, 0, 1] }}
                          style={{ width: `${(s.count / max) * 100}%`, transformOrigin: 'left' }}
                          className="h-full rounded-full bg-gradient-to-r from-clay/60 to-clay"
                        />
                      </div>
                    </div>
                  )
                })
              ) : (
                <Empty>Score some jobs first.</Empty>
              )}
            </div>
          </div>
        </Bezel>

        <Bezel className="lg:col-span-7" i={2}>
          <div className="p-6">
            <Eyebrow>Fit score distribution</Eyebrow>
            <div className="mt-5 h-56">
              {byScore.length ? (
                <ResponsiveContainer>
                  <BarChart data={byScore} margin={{ left: -22, right: 8, top: 4 }}>
                    <CartesianGrid stroke={GRID} vertical={false} />
                    <XAxis dataKey="score" tick={TICK} axisLine={false} tickLine={false} />
                    <YAxis tick={TICK} axisLine={false} tickLine={false} allowDecimals={false} />
                    <Tooltip content={<HudTooltip />} cursor={{ fill: 'rgba(255,255,255,0.04)' }} />
                    <Bar dataKey="count" radius={[6, 6, 0, 0]} animationDuration={900}>
                      {byScore.map((b) => (
                        <Cell key={b.score} fill={b.score >= 8 ? CLAY : 'rgba(255,255,255,0.14)'} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <Empty>Distribution appears after scoring.</Empty>
              )}
            </div>
          </div>
        </Bezel>

        <Bezel className="lg:col-span-5" i={3}>
          <div className="p-6">
            <Eyebrow>Claude usage by area</Eyebrow>
            <div className="mt-5 space-y-3">
              {spendAreas.length ? (
                spendAreas.map(([area, v]) => (
                  <div
                    key={area}
                    className="rounded-xl bg-white/[0.025] px-3.5 py-2.5 ring-1 ring-white/[0.05]"
                  >
                    <div className="flex items-baseline justify-between">
                      <span className="truncate pr-3 text-[12.5px] text-mut">{AREA_LABEL[area] ?? area}</span>
                      <span className="tnum text-[13px] font-medium text-ink">
                        ≈ ${v.cost.toFixed(2)}
                      </span>
                    </div>
                    <div className="tnum mt-0.5 text-[10.5px] text-faint">
                      {v.calls.toLocaleString()} calls · {((v.in + v.out) / 1000).toFixed(1)}k tokens
                    </div>
                  </div>
                ))
              ) : (
                <Empty>Usage appears as you run scoring and applies on Claude.</Empty>
              )}
              <p className="pt-1 text-[11px] leading-relaxed text-faint">
                "$" is the API-equivalent value of work your Claude subscription
                covers — your plan is flat-rate, so nothing here is billed.
                {billable > 0.01 && ` Plus $${billable.toFixed(2)} billable on an API key.`}
              </p>
            </div>
          </div>
        </Bezel>

        <OutcomesSection />
      </div>
    </motion.div>
  )
}

function Empty({ children }: { children: string }) {
  return <div className="flex h-full items-center justify-center py-8 text-center text-[13px] italic text-faint">{children}</div>
}
