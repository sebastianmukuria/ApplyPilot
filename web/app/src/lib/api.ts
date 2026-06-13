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
    sub_cost: number
    sub_today: number
    tok_in: number
    tok_out: number
    calls: number
    by_model: Record<string, { cost: number; in: number; out: number; calls: number }>
  }
}

export interface RunStatus {
  active?: boolean
  run_id?: string
  worker_slot?: number
  alive?: boolean
  company?: string
  url?: string
  model?: string
  started?: string
  pid?: number
  needs_you?: boolean
  done?: boolean
  status?: string | null
}

export interface RunsResponse {
  slots: number
  free: number
  runs: RunStatus[]
}

export interface ResumeItem {
  id: string
  name: string
  kind: 'library' | 'base' | 'master'
  size: number
  mtime: number
  is_master: boolean
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
  runs(): Promise<RunsResponse> {
    return http(`/api/runs`)
  },
  startRun(url: string, model: string, supervised?: boolean) {
    return http<RunStatus>(`/api/run`, {
      method: 'POST',
      body: JSON.stringify({ url, model, supervised }),
    })
  },
  stopRun(run_id: string) {
    return http(`/api/run/stop`, { method: 'POST', body: JSON.stringify({ run_id }) })
  },
  stopAll() {
    return http<{ stopped: string }>(`/api/run/stop-all`, { method: 'POST' })
  },
  clearRun(run_id: string) {
    return http(`/api/run/clear`, { method: 'POST', body: JSON.stringify({ run_id }) })
  },
  resumes(): Promise<{ master_exists: boolean; items: ResumeItem[] }> {
    return http(`/api/resumes`)
  },
  selectResume(id: string) {
    return http<{ master_exists: boolean; items: ResumeItem[] }>(`/api/resumes/select`, {
      method: 'POST',
      body: JSON.stringify({ id }),
    })
  },
  async uploadResume(file: File): Promise<ResumeItem> {
    const fd = new FormData()
    fd.append('file', file)
    const res = await fetch('/api/resumes/upload', { method: 'POST', body: fd })
    if (!res.ok) throw new Error(`${res.status} ${await res.text().catch(() => '')}`)
    return res.json()
  },
  resumeFileUrl: (id: string, inline = true) =>
    `/api/resumes/file?id=${encodeURIComponent(id)}${inline ? '&inline=1' : ''}`,
  masterUrl: (inline = true) => `/api/files/master${inline ? '?inline=1' : ''}`,
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
  resumeUrl: (url: string, inline = false) =>
    `/api/files/resume?url=${encodeURIComponent(url)}${inline ? '&inline=1' : ''}`,
  coverUrl: (url: string, inline = false) =>
    `/api/files/cover?url=${encodeURIComponent(url)}${inline ? '&inline=1' : ''}`,
}

/** Subscribe to one run's SSE stream. Returns an unsubscribe fn. */
export function subscribeRunLog(runId: string, handlers: {
  onLog?: (line: string) => void
  onStatus?: (s: RunStatus) => void
  onEnd?: () => void
}): () => void {
  const es = new EventSource(`/api/run/log/stream?run_id=${encodeURIComponent(runId)}`)
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
