<template>
  <div class="boss-panel">
    <!-- 系统状态栏 -->
    <div class="status-bar">
      <div class="status-item" :class="{ active: status.browser_running }">
        <span class="status-dot" :class="status.browser_running ? 'dot-on' : 'dot-off'"></span>
        <span>浏览器</span>
      </div>
      <div class="status-item" :class="{ active: status.monitor_running && !status.monitor_paused }">
        <span class="status-dot" :class="status.monitor_running && !status.monitor_paused ? 'dot-on' : 'dot-off'"></span>
        <span>自动回复</span>
      </div>
      <div class="status-item">
        <span class="status-text">今日投递: {{ status.today_applications || 0 }}/{{ status.daily_limit || 15 }}</span>
      </div>
      <div class="status-item" v-if="status.in_cooldown">
        <span class="status-text" style="color: var(--warning);">风控冷却中</span>
      </div>
    </div>

    <!-- 操作按钮区 -->
    <div class="action-bar">
      <button v-if="!status.browser_running" class="btn btn-primary" @click="startBrowser" :disabled="loading">
        {{ loading ? '启动中...' : '启动浏览器' }}
      </button>
      <button v-else class="btn btn-danger" @click="stopBrowser" :disabled="loading">
        {{ loading ? '停止中...' : '停止浏览器' }}
      </button>
      <button class="btn btn-secondary" @click="relogin" :disabled="loading">重新登录</button>
      <button class="btn btn-secondary" @click="heartbeat" :disabled="loading || !status.browser_running">心跳</button>
      <div class="divider"></div>
      <button v-if="status.monitor_running && !status.monitor_paused" class="btn btn-warning" @click="pauseMonitor" :disabled="!status.browser_running">
        暂停回复
      </button>
      <button v-else class="btn btn-success" @click="resumeMonitor" :disabled="!status.browser_running">
        恢复回复
      </button>
    </div>

    <!-- AI 自动求职（AgentLoop，可选高级功能） -->
    <div class="agent-row">
      <input v-model="agentGoal" class="input" placeholder="AI 自动求职指令，如：搜索广州 Python 岗位，只搜不投" @keyup.enter="runAgent" />
      <button class="btn btn-primary" :disabled="agentRunning || !agentGoal.trim() || !status.browser_running" @click="runAgent">
        {{ agentRunning ? '执行中...' : 'AI 自动求职' }}
      </button>
    </div>
    <div v-if="agentResult" class="agent-result">
      <pre>{{ agentResult }}</pre>
    </div>

    <!-- Tab 切换 -->
    <div class="sub-tabs">
      <button v-for="tab in subTabs" :key="tab.key" :class="['sub-tab', { active: activeSubTab === tab.key }]" @click="activeSubTab = tab.key">
        {{ tab.label }}
        <span v-if="tab.badge" class="sub-badge">{{ tab.badge }}</span>
      </button>
    </div>

    <!-- 搜索 -->
    <div v-show="activeSubTab === 'search'" class="sub-content">
      <div class="search-form">
        <input v-model="searchForm.keyword" placeholder="搜索关键词（如 Python开发）" class="input" @keyup.enter="searchJobs" />
        <select v-model="searchForm.city" class="input input-sm">
          <option value="">全国</option>
          <option v-for="c in cities" :key="c" :value="c">{{ c }}</option>
        </select>
        <input v-model="searchForm.welfare" placeholder="福利筛选（逗号分隔）" class="input input-sm" />
        <button class="btn btn-primary" @click="searchJobs" :disabled="loading || !status.browser_running">
          {{ loading ? '搜索中...' : '搜索' }}
        </button>
        <button class="btn btn-secondary" @click="scanPage" :disabled="loading || !status.browser_running">扫描当前页</button>
        <button class="btn btn-success" @click="scanAndApply" :disabled="loading || !status.browser_running">一键投递</button>
      </div>
      <div class="job-list" v-if="jobs.length">
        <div v-for="job in jobs" :key="job.job_url || job.id" class="job-card">
          <div class="job-header">
            <span class="job-title">{{ job.job_title }}</span>
            <span class="job-salary" v-if="job.salary">{{ job.salary }}</span>
          </div>
          <div class="job-meta">
            <span v-if="job.company">{{ job.company }}</span>
            <span v-if="job.city">{{ job.city }}</span>
            <span v-if="job.experience">{{ job.experience }}</span>
            <span v-if="job.education">{{ job.education }}</span>
            <span v-if="job.is_boss" class="tag tag-boss">老板直招</span>
          </div>
          <div class="job-actions">
            <button class="btn btn-sm btn-primary" @click="applyJob(job)" :disabled="loading || job.status !== 'pending'">
              {{ job.status === 'pending' ? '投递' : job.status === 'greeting_sent' ? '已沟通' : job.status }}
            </button>
            <button class="btn btn-sm btn-secondary" @click="analyzeJob(job)">AI分析</button>
            <button class="btn btn-sm btn-accent" @click="importJobToWorkflow(job)" :disabled="loading">
              导入岗位
            </button>
          </div>
        </div>
      </div>
      <div v-else class="empty-hint">
        <p>暂无岗位数据。请先启动浏览器并搜索岗位。</p>
      </div>
    </div>

    <!-- 会话 -->
    <div v-show="activeSubTab === 'conversations'" class="sub-content">
      <!-- 会话列表 -->
      <template v-if="!currentConversation">
        <div class="conv-list" v-if="conversations.length">
          <div v-for="conv in conversations" :key="conv.id" class="conv-card" :class="{ unread: conv.has_unreplied }" @click="selectConversation(conv)">
            <div class="conv-header">
              <span class="conv-name">{{ conv.hr_name }}</span>
              <span class="conv-interest" v-if="conv.interest_level" :class="'interest-' + conv.interest_level">{{ conv.interest_level }}</span>
            </div>
            <div class="conv-meta">
              <span v-if="conv.job_title">{{ conv.job_title }}</span>
              <span v-if="conv.hr_company">{{ conv.hr_company }}</span>
            </div>
            <div class="conv-last" v-if="conv.last_message_text">{{ conv.last_message_text }}</div>
          </div>
        </div>
        <div v-else class="empty-hint">
          <p>暂无会话。启动浏览器并开启自动回复后，HR 消息会自动出现在这里。</p>
        </div>
      </template>

      <!-- 会话详情 -->
      <template v-else>
        <div class="conv-detail-header">
          <button class="btn btn-secondary btn-sm" @click="backToConversations">← 返回</button>
          <span class="conv-detail-name">{{ currentConversation.hr_name }}</span>
          <span v-if="currentConversation.job_title" class="conv-detail-job">{{ currentConversation.job_title }}</span>
        </div>
        <div class="conv-messages" v-if="currentMessages.length">
          <div v-for="(m, i) in currentMessages" :key="i" class="msg-bubble" :class="m.sender === 'me' ? 'me' : 'hr'">
            <div class="msg-sender">{{ m.sender === 'me' ? '我' : 'HR' }}</div>
            <div class="msg-text">{{ m.content }}</div>
            <div class="msg-time" v-if="m.created_at">{{ m.created_at }}</div>
          </div>
        </div>
        <div v-else class="empty-hint"><p>暂无消息</p></div>
        <div class="conv-send-row">
          <input v-model="sendMessageText" class="input" placeholder="输入消息..." @keyup.enter="sendConversationMessage" />
          <button class="btn btn-primary" :disabled="sendingMsg || !sendMessageText.trim()" @click="sendConversationMessage">
            {{ sendingMsg ? '发送中...' : '发送' }}
          </button>
        </div>
      </template>
    </div>

    <!-- 统计 -->
    <div v-show="activeSubTab === 'stats'" class="sub-content">
      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-value">{{ stats.today_applications || 0 }}</div>
          <div class="stat-label">今日投递</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ stats.today_auto_replies || 0 }}</div>
          <div class="stat-label">今日自动回复</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ stats.today_pending || 0 }}</div>
          <div class="stat-label">待处理</div>
        </div>
      </div>
    </div>

    <!-- 设置 -->
    <div v-show="activeSubTab === 'settings'" class="sub-content">
      <div class="settings-form">
        <div class="setting-row">
          <label>每日投递上限</label>
          <input v-model="settingsForm.daily_apply_limit" class="input input-sm" />
        </div>
        <div class="setting-row">
          <label>自动回复</label>
          <select v-model="settingsForm.auto_reply_enabled" class="input input-sm">
            <option value="true">开启</option>
            <option value="false">关闭</option>
          </select>
        </div>
        <div class="setting-row">
          <label>回复风格</label>
          <select v-model="settingsForm.ai_reply_style" class="input input-sm">
            <option value="professional">正式专业</option>
            <option value="casual">轻松友好</option>
            <option value="enthusiastic">热情积极</option>
          </select>
        </div>
        <div class="setting-row">
          <label>招呼语模式</label>
          <select v-model="settingsForm.greeting_mode" class="input input-sm">
            <option value="template">模板</option>
            <option value="smart">智能</option>
          </select>
        </div>
        <div class="setting-row">
          <label>简历摘要</label>
          <textarea v-model="settingsForm.resume_summary" class="input" rows="4" placeholder="粘贴你的简历摘要，AI 会据此生成更精准的招呼语和回复"></textarea>
        </div>
        <button class="btn btn-primary" @click="saveSettings" :disabled="loading">保存设置</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import {
  bossStatus, bossStart, bossStop, bossRelogin, bossHeartbeat,
  bossPauseMonitor, bossResumeMonitor,
  bossSearchJobs, bossListJobs, bossApply, bossScanAndApply, bossScanPage, bossAnalyze,
  bossConversations, bossConversationMessages, bossSendMessage, bossStats, bossGetSettings, bossUpdateSettings,
  bossImportJob, bossAgentRun,
} from '../../api/index.js'

const props = defineProps({
  sessionId: { type: String, default: '' },
})
const emit = defineEmits(['imported'])

const loading = ref(false)
const status = ref({})
const jobs = ref([])
const conversations = ref([])
const stats = ref({})
const settingsForm = ref({})
const activeSubTab = ref('search')
const searchForm = ref({ keyword: '', city: '', welfare: '' })

const cities = ['北京', '上海', '深圳', '杭州', '广州', '成都', '武汉', '南京', '西安', '长沙']

const subTabs = computed(() => [
  { key: 'search', label: '搜索投递', badge: jobs.value.length || '' },
  { key: 'conversations', label: '会话', badge: conversations.value.filter(c => c.has_unreplied).length || '' },
  { key: 'stats', label: '统计' },
  { key: 'settings', label: '设置' },
])

let pollTimer = null

async function refreshStatus() {
  try {
    const { data } = await bossStatus()
    status.value = data
  } catch {}
}

async function startBrowser() {
  loading.value = true
  try {
    const { data } = await bossStart()
    // 后端对 status=error 会抛 500；这里再兜底处理 warning / already_started
    if (data?.status === 'error') {
      alert('启动失败: ' + (data.message || '未知错误'))
    } else if (data?.status === 'warning') {
      alert('提示: ' + (data.message || '浏览器已启动但登录态异常，请重新登录'))
    } else if (data?.status === 'already_started') {
      alert('浏览器已在运行中')
    } else {
      alert(data?.login_status?.logged_in ? '浏览器已启动（已登录）' : '浏览器已启动，请在弹出的窗口完成扫码登录')
    }
    await refreshStatus()
  } catch (e) { alert('启动失败: ' + (e.response?.data?.detail || e.message)) }
  finally { loading.value = false }
}

async function stopBrowser() {
  loading.value = true
  try {
    await bossStop()
    alert('浏览器已停止')
    await refreshStatus()
  } catch (e) { alert('停止失败: ' + (e.response?.data?.detail || e.message)) }
  finally { loading.value = false }
}

async function relogin() {
  loading.value = true
  try {
    const { data } = await bossRelogin()
    if (data?.status === 'error') {
      alert('登录失败: ' + (data.message || '未知错误'))
    } else {
      alert('已重新打开登录窗口，请在浏览器中扫码登录')
    }
    await refreshStatus()
  } catch (e) { alert('登录失败: ' + (e.response?.data?.detail || e.message)) }
  finally { loading.value = false }
}

async function heartbeat() {
  try { await bossHeartbeat() } catch (e) { alert(e.response?.data?.detail || e.message) }
}

async function pauseMonitor() {
  try { await bossPauseMonitor(); await refreshStatus() } catch {}
}

async function resumeMonitor() {
  try { await bossResumeMonitor(); await refreshStatus() } catch {}
}

async function searchJobs() {
  loading.value = true
  try {
    const { data } = await bossSearchJobs(searchForm.value.keyword, searchForm.value.city, searchForm.value.welfare)
    jobs.value = data.jobs || []
  } catch (e) { alert('搜索失败: ' + (e.response?.data?.detail || e.message)) }
  finally { loading.value = false }
}

/** 供父组件联动调用：从对话区"搜索岗位"触发 Boss 搜索 */
async function performSearch(keyword = '') {
  if (!status.browser_running) {
    alert('BOSS 浏览器未启动，请先点击「启动浏览器」')
    return
  }
  activeSubTab.value = 'search'
  if (keyword) searchForm.value.keyword = keyword
  await searchJobs()
}

defineExpose({ performSearch })

async function scanPage() {
  loading.value = true
  try {
    const { data } = await bossScanPage()
    jobs.value = data.jobs || []
  } catch (e) { alert('扫描失败: ' + (e.response?.data?.detail || e.message)) }
  finally { loading.value = false }
}

async function scanAndApply() {
  if (!confirm('确认扫描当前页并批量投递？')) return
  loading.value = true
  try {
    const { data } = await bossScanAndApply()
    alert(`扫描 ${data.scanned || 0} 个岗位，投递 ${data.applied || 0} 个`)
    await refreshStatus()
  } catch (e) { alert('投递失败: ' + (e.response?.data?.detail || e.message)) }
  finally { loading.value = false }
}

async function applyJob(job) {
  loading.value = true
  try {
    await bossApply(job.job_url)
    job.status = 'greeting_sent'
    await refreshStatus()
  } catch (e) { alert('投递失败: ' + (e.response?.data?.detail || e.message)) }
  finally { loading.value = false }
}

async function analyzeJob(job) {
  loading.value = true
  try {
    const { data } = await bossAnalyze(job.job_url, job.job_title, job.company, job.description || '')
    const score = data.match_score || '?'
    const decision = data.decision || ''
    const gap = data.gap || ''
    alert(`匹配度: ${score}\n建议: ${decision}\n差距: ${gap}`)
  } catch (e) { alert('分析失败: ' + (e.response?.data?.detail || e.message)) }
  finally { loading.value = false }
}

/** 将岗位导入主工作流（CopilotState.job），随后自动切换到对话区触发分析。 */
async function importJobToWorkflow(job) {
  if (!job.job_url) {
    alert('该岗位缺少 URL，无法导入')
    return
  }
  loading.value = true
  try {
    const { data } = await bossImportJob(job.job_url, props.sessionId)
    emit('imported', { sessionId: data.session_id, job: data.job, message: data.message })
    // 由 App.vue 处理：切换到对话区并自动发送"分析这个岗位"
  } catch (e) {
    alert('导入失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

async function loadConversations() {
  try {
    const { data } = await bossConversations()
    conversations.value = data.conversations || []
  } catch {}
}

async function loadStats() {
  try {
    const { data } = await bossStats()
    stats.value = data
  } catch {}
}

async function loadSettings() {
  try {
    const { data } = await bossGetSettings()
    settingsForm.value = {
      daily_apply_limit: data.settings?.daily_apply_limit || '15',
      auto_reply_enabled: data.settings?.auto_reply_enabled || 'false',
      ai_reply_style: data.settings?.ai_reply_style || 'professional',
      greeting_mode: data.settings?.greeting_mode || 'template',
      resume_summary: data.settings?.resume_summary || '',
    }
  } catch {}
}

async function saveSettings() {
  loading.value = true
  try {
    await bossUpdateSettings(settingsForm.value)
    alert('设置已保存')
  } catch (e) { alert('保存失败: ' + (e.response?.data?.detail || e.message)) }
  finally { loading.value = false }
}

// ── 会话详情与发消息 ──
const currentConversation = ref(null)
const currentMessages = ref([])
const sendMessageText = ref('')
const sendingMsg = ref(false)

async function selectConversation(conv) {
  currentConversation.value = conv
  try {
    const { data } = await bossConversationMessages(conv.id, 100)
    currentMessages.value = data.messages || []
  } catch (e) {
    alert('加载会话失败: ' + (e.response?.data?.detail || e.message))
  }
}

function backToConversations() {
  currentConversation.value = null
  currentMessages.value = []
  sendMessageText.value = ''
}

async function sendConversationMessage() {
  if (!currentConversation.value || !sendMessageText.value.trim()) return
  sendingMsg.value = true
  const content = sendMessageText.value.trim()
  sendMessageText.value = ''
  try {
    await bossSendMessage(currentConversation.value.id, content)
    // 重新加载消息
    const { data } = await bossConversationMessages(currentConversation.value.id, 100)
    currentMessages.value = data.messages || []
  } catch (e) {
    alert('发送失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    sendingMsg.value = false
  }
}

// ── AI 自动求职（AgentLoop）──
const agentGoal = ref('')
const agentRunning = ref(false)
const agentResult = ref('')

async function runAgent() {
  if (!agentGoal.value.trim()) return
  agentRunning.value = true
  agentResult.value = ''
  const goal = agentGoal.value.trim()
  try {
    const { data } = await bossAgentRun(goal, 12)
    const summary = data.summary || data.result || JSON.stringify(data).slice(0, 500)
    agentResult.value = typeof summary === 'string' ? summary : JSON.stringify(summary, null, 2)
  } catch (e) {
    alert('Agent 执行失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    agentRunning.value = false
  }
}

onMounted(() => {
  refreshStatus()
  loadConversations()
  loadStats()
  loadSettings()
  bossListJobs().then(({ data }) => { jobs.value = data.jobs || [] }).catch(() => {})
  pollTimer = setInterval(() => {
    refreshStatus()
    if (activeSubTab.value === 'conversations') loadConversations()
    if (activeSubTab.value === 'stats') loadStats()
  }, 15000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<style scoped>
.boss-panel { display: flex; flex-direction: column; gap: 12px; }

.status-bar {
  display: flex; gap: 16px; align-items: center;
  padding: 10px 16px; border-radius: 8px;
  background: var(--surface); border: 1px solid var(--border);
}
.status-item { display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--text-light); }
.status-item.active { color: var(--success); }
.status-dot { width: 8px; height: 8px; border-radius: 50%; }
.dot-on { background: #22c55e; box-shadow: 0 0 6px rgba(34,197,94,.4); }
.dot-off { background: var(--text-lighter); }

.action-bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.divider { width: 1px; height: 24px; background: var(--border); margin: 0 4px; }

.btn {
  padding: 7px 14px; border-radius: 6px; font-size: 13px; font-weight: 500;
  border: none; cursor: pointer; transition: all .15s; white-space: nowrap;
}
.btn:disabled { opacity: .5; cursor: not-allowed; }
.btn-sm { padding: 4px 10px; font-size: 12px; }
.btn-primary { background: var(--primary); color: #fff; }
.btn-primary:hover:not(:disabled) { filter: brightness(1.1); }
.btn-secondary { background: var(--surface-dark); color: var(--text-secondary); border: 1px solid var(--border); }
.btn-secondary:hover:not(:disabled) { background: var(--border); }
.btn-success { background: #22c55e; color: #fff; }
.btn-success:hover:not(:disabled) { filter: brightness(1.1); }
.btn-warning { background: #f59e0b; color: #fff; }
.btn-warning:hover:not(:disabled) { filter: brightness(1.1); }
.btn-danger { background: #ef4444; color: #fff; }
.btn-danger:hover:not(:disabled) { filter: brightness(1.1); }
.btn-accent { background: #8b5cf6; color: #fff; }
.btn-accent:hover:not(:disabled) { filter: brightness(1.1); }

.sub-tabs { display: flex; gap: 2px; border-bottom: 1px solid var(--border); overflow-x: auto; }
.sub-tab {
  padding: 6px 12px; font-size: 13px; border: none; background: none;
  cursor: pointer; color: var(--text-light); border-bottom: 2px solid transparent;
  transition: all .15s; white-space: nowrap;
}
.sub-tab:hover { color: var(--text-secondary); }
.sub-tab.active { color: var(--primary); border-bottom-color: var(--primary); }
.sub-badge {
  background: var(--primary); color: #fff; font-size: 10px;
  padding: 0 6px; height: 16px; min-width: 18px; border-radius: 10px;
  display: inline-flex; align-items: center; justify-content: center; margin-left: 4px;
}

.sub-content { display: flex; flex-direction: column; gap: 10px; }

.search-form { display: flex; gap: 8px; flex-wrap: wrap; }
.input {
  flex: 1; min-width: 180px; padding: 7px 12px; border-radius: 6px;
  border: 1px solid var(--border); font-size: 13px; background: var(--bg-white);
  color: var(--text-primary);
}
.input-sm { flex: 0 0 auto; min-width: 100px; }
.input:focus { outline: none; border-color: var(--primary); }

.job-list { display: flex; flex-direction: column; gap: 8px; }
.job-card {
  padding: 12px 16px; border-radius: 8px; background: var(--bg-white);
  border: 1px solid var(--border); transition: all .15s;
}
.job-card:hover { box-shadow: 0 2px 8px rgba(0,0,0,.06); }
.job-header { display: flex; justify-content: space-between; align-items: center; }
.job-title { font-weight: 600; font-size: 14px; color: var(--text-primary); }
.job-salary { color: var(--primary); font-weight: 600; font-size: 13px; }
.job-meta { display: flex; gap: 12px; margin-top: 4px; font-size: 12px; color: var(--text-light); flex-wrap: wrap; }
.job-actions { display: flex; gap: 6px; margin-top: 8px; }
.tag { font-size: 11px; padding: 2px 8px; border-radius: 4px; }
.tag-boss { background: #fef3c7; color: #92400e; }

.agent-row { display: flex; gap: 8px; align-items: center; }
.agent-result {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 14px;
  font-size: 12px;
  max-height: 200px;
  overflow: auto;
  color: var(--text-secondary);
}
.agent-result pre { margin: 0; white-space: pre-wrap; word-break: break-all; }

.conv-detail-header {
  display: flex; align-items: center; gap: 10px;
  padding-bottom: 10px; border-bottom: 1px solid var(--border);
}
.conv-detail-name { font-weight: 600; font-size: 14px; }
.conv-detail-job { font-size: 12px; color: var(--text-light); }
.conv-messages {
  display: flex; flex-direction: column; gap: 8px;
  max-height: 320px; overflow-y: auto; padding: 8px 0;
}
.msg-bubble {
  max-width: 78%; padding: 8px 12px; border-radius: 10px; font-size: 13px;
}
.msg-bubble.hr { align-self: flex-start; background: var(--surface); border: 1px solid var(--border); }
.msg-bubble.me { align-self: flex-end; background: var(--primary); color: #fff; }
.msg-sender { font-size: 11px; opacity: 0.8; margin-bottom: 3px; }
.msg-time { font-size: 10px; opacity: 0.65; margin-top: 3px; }
.conv-send-row { display: flex; gap: 8px; margin-top: 8px; }

.conv-list { display: flex; flex-direction: column; gap: 6px; }
.conv-card {
  padding: 10px 14px; border-radius: 8px; background: var(--bg-white);
  border: 1px solid var(--border); cursor: pointer; transition: all .15s;
}
.conv-card.unread { border-left: 3px solid var(--primary); }
.conv-card:hover { box-shadow: 0 2px 8px rgba(0,0,0,.06); }
.conv-header { display: flex; justify-content: space-between; align-items: center; }
.conv-name { font-weight: 600; font-size: 13px; }
.conv-interest { font-size: 11px; padding: 2px 8px; border-radius: 4px; }
.interest-high { background: #dcfce7; color: #166534; }
.interest-medium { background: #fef9c3; color: #854d0e; }
.interest-low { background: #fee2e2; color: #991b1b; }
.conv-meta { display: flex; gap: 8px; font-size: 12px; color: var(--text-light); margin-top: 2px; }
.conv-last { font-size: 12px; color: var(--text-light); margin-top: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.stats-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.stat-card { padding: 20px; border-radius: 8px; background: var(--bg-white); border: 1px solid var(--border); text-align: center; }
.stat-value { font-size: 28px; font-weight: 700; color: var(--primary); }
.stat-label { font-size: 12px; color: var(--text-light); margin-top: 4px; }

.settings-form { display: flex; flex-direction: column; gap: 10px; max-width: 500px; }
.setting-row { display: flex; flex-direction: column; gap: 4px; }
.setting-row label { font-size: 12px; color: var(--text-light); font-weight: 500; }
.setting-row .input { flex: 1; }

.empty-hint { padding: 40px; text-align: center; color: var(--text-lighter); font-size: 13px; }
</style>
