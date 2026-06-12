// Typed client for the ApplyPilot v2 API (src/applypilot/server.py).

export interface Job {
  url: string
  company: string | null
  title: string | null
  site: string | null
  location: string | null
  salary: string | null
  salary_num: number
  fit_score: number | null
  apply_status: string | null
  flags: string[]
  docs_ready: boolean
  has_resume: boolean
  has_cover: boolean
  score_reasoning: string | null
  discovered_at: string | null
  application_url: string | null
  hidden: boolean
}

export interface JobsResponse {
  total_matched: number
  jobs: Job[]
}

export interface JobsQuery {
  min_score?: number
  sources?: string[]
  search?: string
  sort?: 'score' | 'salary' | 'company' | 'location' | 'recent'
  show_applied?: boolean
  only_docs_ready?: boolean
  include_no_salary?: boolean
  min_salary_k?: number
  hide_flagged?: boolean
  hidden?: boolean
  limit?: number
  offset?: number
}

export interface Stats {
  funnel: Record<string, number>
  per_day: { date: string; applications: number }[]
  status_breakdown: { status: string; count: number }[]
  score_distribution: { score: number; count: number }[]
  spend: {
    cost: number
    today: number
    tok_in: number
    tok_out: number
    calls: number
    by_model: Record<string, { cost: number; in: number; out: number; calls: number }>
  }
}

export interface RunStatus {
  active: boolean
  company?: string
  url?: string
  model?: string
  started?: string
  pid?: number
  needs_you?: boolean
  done?: boolean
  status?: string | null
}

export interface Settings {
  supervised: boolean
  fixed_resume: boolean
  salary_mode: string
  salary_fixed: string
  llm_model: string
  telegram_connected: boolean
  master_resume_exists: boolean
}

export interface WorkContext {
  projects: { name: string; what: string; tools: string; impact: string }[]
  llm_usage: string
  answer_rules: string
}

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new Error(`${res.status} ${res.statusText}${body ? ` — ${body.slice(0, 200)}` : ''}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  jobs(q: JobsQuery = {}): Promise<JobsResponse> {
    const p = new URLSearchParams()
    for (const [k, v] of Object.entries(q)) {
      if (v === undefined || v === null) continue
      p.set(k, Array.isArray(v) ? v.join(',') : String(v))
    }
    return http(`/api/jobs?${p}`)
  },
  jobDetail(url: string) {
    return http<Job & { resume_preview: string; cover_preview: string }>(
      `/api/jobs/detail?url=${encodeURIComponent(url)}`,
    )
  },
  handoffs(): Promise<{ jobs: Job[] } | Job[]> {
    return http(`/api/jobs/handoffs`)
  },
  markApplied(url: string) {
    return http(`/api/jobs/mark-applied`, { method: 'POST', body: JSON.stringify({ url }) })
  },
  resetJob(url: string) {
    return http(`/api/jobs/reset`, { method: 'POST', body: JSON.stringify({ url }) })
  },
  hide(url: string) {
    return http(`/api/jobs/hide`, { method: 'POST', body: JSON.stringify({ url }) })
  },
  unhide(url: string) {
    return http(`/api/jobs/unhide`, { method: 'POST', body: JSON.stringify({ url }) })
  },
  stats(): Promise<Stats> {
    return http(`/api/stats`)
  },
  run(): Promise<RunStatus> {
    return http(`/api/run`)
  },
  startRun(url: string, model: string, supervised?: boolean) {
    return http<RunStatus>(`/api/run`, {
      method: 'POST',
      body: JSON.stringify({ url, model, supervised }),
    })
  },
  stopRun() {
    return http<{ stopped: string }>(`/api/run/stop`, { method: 'POST' })
  },
  clearRun() {
    return http(`/api/run/clear`, { method: 'POST' })
  },
  runLog(): Promise<{ lines: string }> {
    return http(`/api/run/log`)
  },
  settings(): Promise<Settings> {
    return http(`/api/settings`)
  },
  saveSettings(patch: Partial<Settings>) {
    return http<Settings>(`/api/settings`, { method: 'PUT', body: JSON.stringify(patch) })
  },
  workContext(): Promise<WorkContext> {
    return http(`/api/work-context`)
  },
  saveWorkContext(wc: WorkContext) {
    return http(`/api/work-context`, { method: 'PUT', body: JSON.stringify(wc) })
  },
  genAnswer(body: { company: string; question: string; length?: string; prev?: string }) {
    return http<{ answer: string }>(`/api/answers`, { method: 'POST', body: JSON.stringify(body) })
  },
  resumeUrl: (url: string) => `/api/files/resume?url=${encodeURIComponent(url)}`,
  coverUrl: (url: string) => `/api/files/cover?url=${encodeURIComponent(url)}`,
}

/** Subscribe to the live-run SSE stream. Returns an unsubscribe fn. */
export function subscribeRunLog(handlers: {
  onLog?: (line: string) => void
  onStatus?: (s: RunStatus) => void
  onEnd?: () => void
}): () => void {
  const es = new EventSource('/api/run/log/stream')
  es.addEventListener('log', (e) => handlers.onLog?.((e as MessageEvent).data))
  es.addEventListener('status', (e) => {
    try {
      handlers.onStatus?.(JSON.parse((e as MessageEvent).data))
    } catch {
      /* ignore malformed status frames */
    }
  })
  es.onerror = () => {
    es.close()
    handlers.onEnd?.()
  }
  return () => es.close()
}
