// Answers: draft truthful application answers + edit the work_context dossier.
import { useEffect, useState } from 'react'
import { motion } from 'motion/react'
import { CircleNotch, FloppyDisk, PaperPlaneRight, Plus, TrashSimple } from '@phosphor-icons/react'
import { api, type WorkContext } from '../lib/api'
import { stagger } from '../lib/motion'
import { Bezel, Eyebrow, IslandButton } from '../components/ui'

const inputCls =
  'w-full rounded-2xl bg-white/[0.03] px-4 py-3 text-[13.5px] text-ink ring-1 ring-white/[0.07] outline-none transition-shadow duration-300 placeholder:text-faint focus:ring-clay/40'

export function Answers() {
  const [company, setCompany] = useState('')
  const [length, setLength] = useState('2-3 sentences')
  const [question, setQuestion] = useState('')
  const [prev, setPrev] = useState('')
  const [answer, setAnswer] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const [wc, setWc] = useState<WorkContext | null>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    api.workContext().then(setWc).catch(() => setWc({ projects: [], llm_usage: '', answer_rules: '' }))
  }, [])

  const generate = async () => {
    if (!question.trim()) return
    setBusy(true)
    setErr('')
    try {
      const r = await api.genAnswer({ company, question, length, prev })
      setAnswer(r.answer)
    } catch (e) {
      setErr(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <motion.div variants={stagger} initial="hidden" animate="show" className="mx-auto w-full max-w-7xl px-4 pb-32">
      <div className="grid gap-4 lg:grid-cols-12">
        <Bezel className="lg:col-span-7" i={0}>
          <div className="space-y-4 p-7">
            <Eyebrow>Draft an answer</Eyebrow>
            <p className="text-[13px] leading-relaxed text-mut">
              Grounded in your real projects — it never merges them or invents tools.
            </p>
            <div className="grid gap-3 sm:grid-cols-[1fr_180px]">
              <input className={inputCls} placeholder="Company / role" value={company} onChange={(e) => setCompany(e.target.value)} />
              <input className={inputCls} placeholder="Length" value={length} onChange={(e) => setLength(e.target.value)} />
            </div>
            <textarea
              className={`${inputCls} min-h-24 resize-y`}
              placeholder="Paste the application question…"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
            />
            <textarea
              className={`${inputCls} min-h-16 resize-y`}
              placeholder="Previous answer to improve (optional)"
              value={prev}
              onChange={(e) => setPrev(e.target.value)}
            />
            <IslandButton
              onClick={generate}
              disabled={busy || !question.trim()}
              icon={busy ? <CircleNotch size={13} className="animate-spin" /> : <PaperPlaneRight weight="fill" size={12} />}
            >
              {busy ? 'Drafting' : 'Generate answer'}
            </IslandButton>
            {err && <p className="text-[12px] text-clay-hi">{err}</p>}
            {answer && (
              <motion.div
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                className="rounded-2xl bg-clay/[0.06] p-5 ring-1 ring-clay/20"
              >
                <div className="mb-2 flex items-center justify-between">
                  <span className="font-display text-[11px] uppercase tracking-[0.2em] text-clay-hi">Answer</span>
                  <button
                    className="text-[11px] text-mut transition-colors hover:text-ink"
                    onClick={() => navigator.clipboard.writeText(answer)}
                  >
                    Copy
                  </button>
                </div>
                <p className="text-[14px] leading-relaxed text-ink">{answer}</p>
              </motion.div>
            )}
          </div>
        </Bezel>

        <Bezel className="lg:col-span-5" i={1}>
          <div className="space-y-4 p-7">
            <div className="flex items-center justify-between">
              <Eyebrow>Your projects</Eyebrow>
              {wc && (
                <IslandButton
                  variant="ghost"
                  onClick={async () => {
                    await api.saveWorkContext(wc)
                    setSaved(true)
                    setTimeout(() => setSaved(false), 2000)
                  }}
                  icon={<FloppyDisk weight="light" size={13} />}
                  className="!px-4 !py-1.5 !text-[12px]"
                >
                  {saved ? 'Saved' : 'Save'}
                </IslandButton>
              )}
            </div>
            {!wc ? (
              <div className="py-10 text-center text-[13px] italic text-faint">Loading dossier…</div>
            ) : (
              <div className="space-y-3">
                {wc.projects.map((p, i) => (
                  <details key={i} className="group rounded-2xl bg-white/[0.025] ring-1 ring-white/[0.06] open:ring-white/15">
                    <summary className="flex cursor-pointer items-center justify-between px-4 py-3 text-[13px] text-ink marker:content-none">
                      <span className="truncate">{p.name || `Project ${i + 1}`}</span>
                      <button
                        className="text-faint transition-colors hover:text-clay-hi"
                        onClick={(e) => {
                          e.preventDefault()
                          setWc({ ...wc, projects: wc.projects.filter((_, j) => j !== i) })
                        }}
                      >
                        <TrashSimple weight="light" size={14} />
                      </button>
                    </summary>
                    <div className="space-y-2 px-4 pb-4">
                      {(['name', 'what', 'tools', 'impact'] as const).map((field) => (
                        <input
                          key={field}
                          className={`${inputCls} !py-2 !text-[12.5px]`}
                          placeholder={field}
                          value={p[field]}
                          onChange={(e) => {
                            const projects = [...wc.projects]
                            projects[i] = { ...projects[i], [field]: e.target.value }
                            setWc({ ...wc, projects })
                          }}
                        />
                      ))}
                    </div>
                  </details>
                ))}
                <button
                  className="flex w-full items-center justify-center gap-2 rounded-2xl py-3 text-[12.5px] text-mut ring-1 ring-dashed ring-white/15 transition-all hover:text-ink hover:ring-white/30"
                  onClick={() =>
                    setWc({ ...wc, projects: [...wc.projects, { name: '', what: '', tools: '', impact: '' }] })
                  }
                >
                  <Plus weight="light" size={14} /> Add project
                </button>
                <textarea
                  className={`${inputCls} min-h-16 !text-[12.5px]`}
                  placeholder="LLM / tooling facts"
                  value={wc.llm_usage}
                  onChange={(e) => setWc({ ...wc, llm_usage: e.target.value })}
                />
                <textarea
                  className={`${inputCls} min-h-16 !text-[12.5px]`}
                  placeholder="Answer rules"
                  value={wc.answer_rules}
                  onChange={(e) => setWc({ ...wc, answer_rules: e.target.value })}
                />
              </div>
            )}
          </div>
        </Bezel>
      </div>
    </motion.div>
  )
}
