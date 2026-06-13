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
  outcome?: string | null
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
  stage?: string
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
    sub_tok_in: number
    sub_tok_out: number
    sub_calls: number
    sub_by_area: Record<string, { cost: number; in: number; out: number; calls: number }>
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
  cover_provider?: string
  claude_cli_available?: boolean
}

export interface OutcomesSummary {
  funnel: Record<string, number>
  by_score: { band: string; applied: number; responded: number; response_rate: number }[]
  by_source: { site: string; applied: number; responded: number; response_rate: number }[]
}

export interface AppEvent {
  id: number
  company: string
  role: string
  event_type: string
  confidence: number
  email_ts: string | null
  subject: string | null
  job_url: string | null
  job_company?: string | null
  job_title?: string | null
}

export interface TrackingStatus {
  configured: boolean
  has_credentials?: boolean
  connect_phase?: 'idle' | 'opening' | 'connected' | 'error'
  last_sync: string | null
  backfill: { phase: string; scanned: number; total: number; events: number; done_at?: string } | null
  events_total: number
  matched_total: number
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
  setOutcome(url: string, outcome: string) {
    return http(`/api/jobs/outcome`, { method: 'POST', body: JSON.stringify({ url, outcome }) })
  },
  outcomesSummary(): Promise<OutcomesSummary> {
    return http(`/api/outcomes/summary`)
  },
  outcomesEvents(limit = 30): Promise<{ events: AppEvent[] } | AppEvent[]> {
    return http(`/api/outcomes/events?limit=${limit}`)
  },
  trackingStatus(): Promise<TrackingStatus> {
    return http(`/api/tracking/status`)
  },
  trackingConnect() {
    return http<{ phase: string }>(`/api/tracking/connect`, { method: 'POST' })
  },
  trackingDisconnect() {
    return http(`/api/tracking/disconnect`, { method: 'POST' })
  },
  trackingSync() {
    return http<{ scanned: number; events: number; outcomes_set: number }>(`/api/tracking/sync`, { method: 'POST' })
  },
  trackingBackfill(days = 90) {
    return http(`/api/tracking/backfill`, { method: 'POST', body: JSON.stringify({ days }) })
  },
  resumeDocxUrl: (url: string) => `/api/files/resume?url=${encodeURIComponent(url)}&fmt=docx`,
  coverDocxUrl: (url: string) => `/api/files/cover?url=${encodeURIComponent(url)}&fmt=docx`,
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
  es.addEventListener('log', (e) => {
    // the server sends {"line": "..."}; unwrap to the raw text
    const raw = (e as MessageEvent).data
    try {
      handlers.onLog?.(JSON.parse(raw).line ?? raw)
    } catch {
      handlers.onLog?.(raw)
    }
  })
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
