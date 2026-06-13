// Run settings: glass modal — supervised, resume mode, salary strategy, model.
import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { X } from '@phosphor-icons/react'
import { Books } from '@phosphor-icons/react'
import { api, type Settings } from '../lib/api'
import { enableBrowserNotifications, type AlertPrefs } from '../lib/alerts'
import { DECK } from '../lib/motion'
import { ResumeLibrary } from './ResumeLibrary'
import { Eyebrow } from './ui'

const MODELS = ['sonnet', 'haiku', 'opus']
const SALARY_MODES = [
  { id: 'posting', label: 'Match the posting' },
  { id: 'blank', label: 'Leave blank' },
  { id: 'fixed', label: 'Fixed amount' },
]

export function SettingsSheet({
  open,
  onClose,
  model,
  onModel,
  alertPrefs,
  onAlertPrefs,
}: {
  open: boolean
  onClose: () => void
  model: string
  onModel: (m: string) => void
  alertPrefs: AlertPrefs
  onAlertPrefs: (p: AlertPrefs) => void
}) {
  const [s, setS] = useState<Settings | null>(null)
  const [saved, setSaved] = useState(false)
  const [libOpen, setLibOpen] = useState(false)

  useEffect(() => {
    if (open) api.settings().then(setS).catch(() => {})
  }, [open])

  const patch = async (p: Partial<Settings>) => {
    if (!s) return
    const next = { ...s, ...p }
    setS(next)
    await api.saveSettings(p)
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-2xl"
          onClick={onClose}
        >
          <motion.div
            initial={{ y: 32, scale: 0.97, opacity: 0 }}
            animate={{ y: 0, scale: 1, opacity: 1 }}
            exit={{ y: 20, scale: 0.98, opacity: 0 }}
            transition={{ duration: 0.5, ease: DECK }}
            className="shell w-full max-w-lg"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="core space-y-6 p-7">
              <div className="flex items-center justify-between">
                <Eyebrow>Run settings</Eyebrow>
                <div className="flex items-center gap-3">
                  <AnimatePresence>
                    {saved && (
                      <motion.span
                        initial={{ opacity: 0, x: 6 }}
                        animate={{ opacity: 1, x: 0 }}
                        exit={{ opacity: 0 }}
                        className="text-[11px] text-sage"
                      >
                        Saved
                      </motion.span>
                    )}
                  </AnimatePresence>
                  <button onClick={onClose} className="text-mut transition-colors hover:text-ink">
                    <X weight="light" size={18} />
                  </button>
                </div>
              </div>

              {!s ? (
                <div className="py-8 text-center text-[13px] italic text-faint">Reading instruments…</div>
              ) : (
                <>
                  <Row
                    title="Supervised mode"
                    desc="The agent fills everything, pings your phone, and leaves Submit to you."
                  >
                    <Switch on={s.supervised} set={(v) => patch({ supervised: v })} />
                  </Row>

                  <Row
                    title="Fixed master résumé"
                    desc={
                      s.master_resume_exists
                        ? 'Every application uploads your one chosen PDF; only cover letters are generated.'
                        : 'Pick or upload a PDF in the library to enable.'
                    }
                  >
                    <Switch on={s.fixed_resume} set={(v) => patch({ fixed_resume: v })} />
                  </Row>
                  <button
                    onClick={() => setLibOpen(true)}
                    className="flex w-full items-center justify-center gap-2 rounded-2xl py-2.5 text-[12.5px] text-mut ring-1 ring-white/[0.08] transition-all duration-300 hover:text-ink hover:ring-white/20"
                  >
                    <Books weight="light" size={15} /> Résumé library — preview &amp; choose the master
                  </button>

                  <div>
                    <div className="mb-2 text-[13px] text-ink">Salary answers</div>
                    <div className="flex flex-wrap gap-1.5">
                      {SALARY_MODES.map((m) => (
                        <Chip key={m.id} active={s.salary_mode === m.id} onClick={() => patch({ salary_mode: m.id })}>
                          {m.label}
                        </Chip>
                      ))}
                    </div>
                    {s.salary_mode === 'fixed' && (
                      <input
                        className="mt-2.5 w-full rounded-2xl bg-white/[0.03] px-4 py-2.5 text-[13px] text-ink ring-1 ring-white/[0.07] outline-none focus:ring-clay/40"
                        placeholder="e.g. 145000 or 140000-160000"
                        defaultValue={s.salary_fixed}
                        onBlur={(e) => patch({ salary_fixed: e.target.value })}
                      />
                    )}
                  </div>

                  <div>
                    <div className="mb-2 text-[13px] text-ink">Apply model</div>
                    <div className="flex gap-1.5">
                      {MODELS.map((m) => (
                        <Chip key={m} active={model === m} onClick={() => onModel(m)}>
                          {m}
                        </Chip>
                      ))}
                    </div>
                  </div>

                  <div className="space-y-3 border-t border-white/[0.07] pt-4">
                    <Eyebrow>Alerts</Eyebrow>
                    <Row title="Browser notifications" desc="OS banner when a run needs you or finishes.">
                      <Switch
                        on={alertPrefs.browser}
                        set={async (v) => {
                          if (v && !(await enableBrowserNotifications())) return
                          onAlertPrefs({ ...alertPrefs, browser: v })
                        }}
                      />
                    </Row>
                    <Row title="Chime" desc="A soft tone when a run needs you.">
                      <Switch on={alertPrefs.chime} set={(v) => onAlertPrefs({ ...alertPrefs, chime: v })} />
                    </Row>
                    <ChannelFields s={s} onSaved={() => api.settings().then(setS).catch(() => {})} />
                  </div>

                  <div className="flex items-center gap-4 border-t border-white/[0.07] pt-4 text-[11.5px] text-faint">
                    <span className="flex items-center gap-1.5">
                      <span className={`h-1.5 w-1.5 rounded-full ${s.telegram_connected ? 'bg-sage' : 'bg-white/20'}`} />
                      Telegram {s.telegram_connected ? 'connected' : 'off'}
                    </span>
                    <span>·</span>
                    <span className="truncate">LLM {s.llm_model || '?'}</span>
                  </div>
                </>
              )}
            </div>
          </motion.div>
          <ResumeLibrary
            open={libOpen}
            onClose={() => {
              setLibOpen(false)
              api.settings().then(setS).catch(() => {})
            }}
          />
        </motion.div>
      )}
    </AnimatePresence>
  )
}

function Row({ title, desc, children }: { title: string; desc: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-6">
      <div>
        <div className="text-[13px] text-ink">{title}</div>
        <div className="mt-0.5 text-[11.5px] leading-relaxed text-faint">{desc}</div>
      </div>
      {children}
    </div>
  )
}

function Switch({ on, set }: { on: boolean; set: (v: boolean) => void }) {
  return (
    <button
      onClick={() => set(!on)}
      className={`relative h-6 w-11 flex-none rounded-full transition-colors duration-300 ${on ? 'bg-clay' : 'bg-white/10'}`}
    >
      <span
        className={`absolute left-0.5 top-0.5 h-5 w-5 rounded-full bg-ink transition-transform duration-300 ease-[cubic-bezier(0.32,0.72,0,1)] ${
          on ? 'translate-x-[20px]' : 'translate-x-0'
        }`}
      />
    </button>
  )
}

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`rounded-full px-3.5 py-1.5 text-[12px] transition-all duration-300 ${
        active ? 'bg-clay/15 text-clay-hi ring-1 ring-clay/30' : 'text-mut ring-1 ring-white/[0.07] hover:text-ink'
      }`}
    >
      {children}
    </button>
  )
}


interface ChannelSettings extends Settings {
  ntfy_configured?: boolean
  webhook_configured?: boolean
  discord_configured?: boolean
  slack_configured?: boolean
}

/** Push channels (ntfy / webhooks) — write-only fields; rendered only once the
 * backend exposes the *_configured flags. */
function ChannelFields({ s, onSaved }: { s: ChannelSettings; onSaved: () => void }) {
  if (s.ntfy_configured === undefined) return null
  const channels: { key: string; label: string; placeholder: string; configured: boolean }[] = [
    { key: 'ntfy_topic', label: 'ntfy topic', placeholder: 'e.g. applypilot-x7k2', configured: !!s.ntfy_configured },
    { key: 'discord_webhook_url', label: 'Discord webhook', placeholder: 'https://discord.com/api/webhooks/…', configured: !!s.discord_configured },
    { key: 'slack_webhook_url', label: 'Slack webhook', placeholder: 'https://hooks.slack.com/…', configured: !!s.slack_configured },
    { key: 'webhook_url', label: 'Custom webhook', placeholder: 'https://…', configured: !!s.webhook_configured },
  ]
  return (
    <div className="space-y-2">
      {channels.map((c) => (
        <div key={c.key} className="flex items-center gap-2.5">
          <span className="flex w-32 flex-none items-center gap-1.5 text-[11.5px] text-mut">
            <span className={`h-1.5 w-1.5 rounded-full ${c.configured ? 'bg-sage' : 'bg-white/15'}`} />
            {c.label}
          </span>
          <input
            className="min-w-0 flex-1 rounded-xl bg-white/[0.03] px-3 py-2 text-[12px] text-ink ring-1 ring-white/[0.06] outline-none placeholder:text-faint focus:ring-clay/40"
            placeholder={c.configured ? 'configured — paste to replace, empty to remove' : c.placeholder}
            defaultValue=""
            onBlur={async (e) => {
              const v = e.target.value.trim()
              if (!v && !c.configured) return
              if (!v && c.configured && !confirm(`Remove the ${c.label}?`)) return
              try {
                await api.saveSettings({ [c.key]: v } as Partial<Settings>)
                e.target.value = ''
                onSaved()
              } catch (err) {
                alert(String(err))
              }
            }}
          />
        </div>
      ))}
    </div>
  )
}
