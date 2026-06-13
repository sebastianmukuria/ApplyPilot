// Intel: animated charts over the campaign data.
import { useMemo } from 'react'
import { motion } from 'motion/react'
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import type { Stats } from '../lib/api'
import { stagger } from '../lib/motion'
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
  const spendModels = useMemo(
    () => Object.entries(stats?.spend.by_model ?? {}).sort((a, b) => b[1].cost - a[1].cost),
    [stats],
  )

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
            <Eyebrow>Spend by model</Eyebrow>
            <div className="mt-5 space-y-3">
              {spendModels.length ? (
                spendModels.map(([model, v]) => (
                  <div
                    key={model}
                    className="flex items-baseline justify-between rounded-xl bg-white/[0.025] px-3.5 py-2.5 ring-1 ring-white/[0.05]"
                  >
                    <span className="truncate pr-3 text-[12.5px] text-mut">{model}</span>
                    <span className="tnum text-[13px] font-medium text-ink">${v.cost.toFixed(2)}</span>
                  </div>
                ))
              ) : (
                <Empty>Usage tracking starts with your next pipeline run.</Empty>
              )}
              <p className="pt-1 text-[11px] leading-relaxed text-faint">
                Billable estimates from the PRICES table. Claude apply rows show the
                API-equivalent value — covered by your subscription, $0 billed.
              </p>
            </div>
          </div>
        </Bezel>
      </div>
    </motion.div>
  )
}

function Empty({ children }: { children: string }) {
  return <div className="flex h-full items-center justify-center py-8 text-center text-[13px] italic text-faint">{children}</div>
}
