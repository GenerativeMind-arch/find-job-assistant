import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 300000, //请求超时时间(毫秒)
  headers: { 'Content-Type': 'application/json' },
})

/** POST /api/chat */
export function sendChat(sessionId, message, attachments = []) {
  return api.post('/chat', {
    session_id: sessionId,
    message,
    attachments,
  })
}

/** GET /api/resume/content */
export function getResumeContent(sessionId) {
  return api.get('/resume/content', { params: { session_id: sessionId } })
}

/** GET /api/resume/html */
export function getResumeHtml(sessionId) {
  return api.get('/resume/html', { params: { session_id: sessionId } })
}

/** POST /api/resume/render */
export function renderResume(sessionId, instruction) {
  return api.post('/resume/render', {
    session_id: sessionId,
    render_instruction: instruction,
  })
}

/** POST /api/export */
export function exportArtifact(sessionId, target = 'resume', format = 'html') {
  return api.post('/export', { session_id: sessionId, target, format }, {
    responseType: 'blob',
  })
}

export async function readApiError(error) {
  const payload = error?.response?.data
  if (payload instanceof Blob) {
    try {
      const text = await payload.text()
      const parsed = JSON.parse(text)
      return parsed?.detail || error.message
    } catch {
      return error.message
    }
  }
  return payload?.detail || error.message
}

/** GET /health */
export function healthCheck() {
  return axios.get('/health')
}

// ══════════════════════════════════════
//  BOSS 直聘自动化 API
// ══════════════════════════════════════

const bossApi = axios.create({
  baseURL: '/api/boss',
  timeout: 300000,
  headers: { 'Content-Type': 'application/json' },
})

/** GET /api/boss/status */
export function bossStatus() {
  return bossApi.get('/status')
}

/** POST /api/boss/system/start
 *  交互流程始终以可见模式启动（headless=false），便于扫码登录与手动操作。 */
export function bossStart() {
  return bossApi.post('/system/start', { headless: false })
}

/** POST /api/boss/system/stop */
export function bossStop() {
  return bossApi.post('/system/stop')
}

/** POST /api/boss/system/relogin */
export function bossRelogin() {
  return bossApi.post('/system/relogin')
}

/** POST /api/boss/system/heartbeat */
export function bossHeartbeat() {
  return bossApi.post('/system/heartbeat')
}

/** POST /api/boss/monitor/pause */
export function bossPauseMonitor() {
  return bossApi.post('/monitor/pause')
}

/** POST /api/boss/monitor/resume */
export function bossResumeMonitor() {
  return bossApi.post('/monitor/resume')
}

/** POST /api/boss/jobs/search — filters: {experience, salary, degree, job_type}（数组或字符串） */
export function bossSearchJobs(keyword, city = '', welfare = '', limit = 60, filters = {}) {
  return bossApi.post('/jobs/search', { keyword, city, welfare, limit, ...filters })
}

/** POST /api/boss/auto-reply — 自动回复总开关 { enabled: boolean } */
export function bossSetAutoReply(enabled) {
  return bossApi.post('/auto-reply', { enabled })
}

/** GET /api/boss/filters — 返回 BOSS 支持的筛选项标签 */
export function bossFilters() {
  return bossApi.get('/filters')
}

/** GET /api/boss/jobs */
export function bossListJobs(status = '', limit = 100) {
  return bossApi.get('/jobs', { params: { status, limit } })
}

/** POST /api/boss/jobs/apply */
export function bossApply(jobUrl, greeting = '') {
  return bossApi.post('/jobs/apply', { job_url: jobUrl, greeting })
}

/** POST /api/boss/jobs/apply-batch
 *  批量投递含 45-120s 的人工间隔，调用方可传 config（如 { timeout: 0 }）覆盖默认超时。 */
export function bossApplyBatch(jobUrls, greeting = '', config = {}) {
  return bossApi.post('/jobs/apply-batch', { job_urls: jobUrls, greeting }, config)
}

/** POST /api/boss/jobs/scan-and-apply */
export function bossScanAndApply(greeting = '') {
  return bossApi.post('/jobs/scan-and-apply', { greeting })
}

/** POST /api/boss/jobs/scan */
export function bossScanPage() {
  return bossApi.post('/jobs/scan')
}

/** POST /api/boss/jobs/analyze */
export function bossAnalyze(jobUrl, jobTitle = '', company = '', description = '') {
  return bossApi.post('/jobs/analyze', { job_url: jobUrl, job_title: jobTitle, company, description })
}

/** GET /api/boss/conversations */
export function bossConversations() {
  return bossApi.get('/conversations')
}

/** GET /api/boss/conversations/:id/messages */
export function bossConversationMessages(convId, limit = 50) {
  return bossApi.get(`/conversations/${convId}/messages`, { params: { limit } })
}

/** POST /api/boss/conversations/:id/send */
export function bossSendMessage(convId, content) {
  return bossApi.post(`/conversations/${convId}/send`, { content })
}

/** GET /api/boss/stats */
export function bossStats() {
  return bossApi.get('/stats')
}

/** GET /api/boss/settings */
export function bossGetSettings() {
  return bossApi.get('/settings')
}

/** PUT /api/boss/settings */
export function bossUpdateSettings(updates) {
  return bossApi.put('/settings', updates)
}

/** GET /api/boss/shortlists */
export function bossShortlists() {
  return bossApi.get('/shortlists')
}

/** POST /api/boss/import-job — 将 Boss 岗位导入主工作流 */
export function bossImportJob(jobUrl, sessionId = '') {
  return bossApi.post('/import-job', { job_url: jobUrl, session_id: sessionId })
}

// ══════════════════════════════════════
//  模拟面试 API
// ══════════════════════════════════════

const interviewApi = axios.create({
  baseURL: '/api/interview',
  timeout: 120000,
  headers: { 'Content-Type': 'application/json' },
})

/** POST /api/interview/start — 开始模拟面试 */
export function interviewStart(sessionId) {
  return interviewApi.post('/start', { session_id: sessionId })
}

/** POST /api/interview/chat — 提交回答（文字或语音） */
export function interviewChat(sessionId, answer = '', audioBase64 = '') {
  return interviewApi.post('/chat', {
    session_id: sessionId,
    answer,
    audio_base64: audioBase64 || null,
  })
}

/** POST /api/interview/end — 结束面试，获取综合评价 */
export function interviewEnd(sessionId) {
  return interviewApi.post('/end', { session_id: sessionId })
}

/** POST /api/interview/transcribe — 独立语音转文字 */
export function interviewTranscribe(audioBase64) {
  return interviewApi.post('/transcribe', { audio_base64: audioBase64 })
}

/** GET /api/boss/agent/tools — Agent 工具列表 */
export function bossAgentTools() {
  return bossApi.get('/agent/tools')
}

/** POST /api/boss/agent/run — 运行 Plan-then-Execute Agent */
export function bossAgentRun(goal, maxSteps = 12) {
  return bossApi.post('/agent/run', { goal, max_steps: maxSteps })
}
