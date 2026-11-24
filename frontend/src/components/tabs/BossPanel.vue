<template>
  <div class="boss-panel">
    <h2 class="boss-heading">BOSS 求职</h2>
    <!-- 系统状态栏 -->
    <div class="status-bar">
      <div class="status-item" :class="{ active: status.browser_running }">
        <span class="status-dot" :class="status.browser_running ? 'dot-on' : 'dot-off'"></span>
        <span>浏览器：{{ status.browser_running ? '运行中' : '未启动' }}</span>
      </div>
      <div class="status-item" :class="{ active: monitorState === 'running' }">
        <span class="status-dot" :class="monitorDotClass"></span>
        <span>{{ monitorLabel }}</span>
      </div>
      <div class="status-item">
        <span class="status-text" :title="budgetTitle">{{ applyBudgetText }}</span>
      </div>
      <div class="status-item" v-if="status.in_cooldown">
        <span class="status-text" style="color: var(--warning);">风控冷却中</span>
      </div>
      <!-- 预算被收缩/用完的原因。不显示的话用户只会看到"额度怎么少了" -->
      <div class="status-item" v-if="budgetReason">
        <span class="status-text" style="color: var(--warning);">{{ budgetReason }}</span>
      </div>
    </div>

    <!-- 浏览器线程卡死：所有浏览器操作都会立即失败，只能重建 -->
    <div class="wedged-banner" v-if="status.browser_wedged" role="alert">
      <b>浏览器无响应</b>：上一次浏览器操作超时未返回，线程已卡死。
      请点「{{ status.browser_running ? '停止浏览器' : '启动浏览器' }}」重建，否则搜索/投递/心跳都会直接失败。
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
      <button
        v-if="monitorState === 'running'"
        class="btn btn-warning"
        :disabled="!status.browser_running || togglingReply"
        @click="toggleAutoReply(false)"
      >
        {{ togglingReply ? '处理中...' : '停止回复' }}
      </button>
      <button
        v-else
        class="btn btn-success"
        :disabled="!status.browser_running || togglingReply"
        @click="toggleAutoReply(true)"
      >
        {{ togglingReply ? '处理中...' : '恢复回复' }}
      </button>
    </div>

    <!-- AI 自动求职（AgentLoop，可选高级功能） -->
    <details class="agent-tools">
      <summary>AI 自动求职</summary>
      <div class="agent-row">
        <input v-model="agentGoal" class="input" aria-label="自动求职指令" placeholder="如：搜索广州 Python 岗位，只搜不投" @keyup.enter="runAgent" />
        <button class="btn btn-primary" :disabled="agentRunning || !agentGoal.trim() || !status.browser_running" @click="runAgent">
          {{ agentRunning ? '执行中...' : '执行指令' }}
        </button>
      </div>
      <div v-if="agentResult" class="agent-result"><pre>{{ agentResult }}</pre></div>
    </details>

    <!-- Tab 切换 -->
    <nav class="sub-tabs" aria-label="BOSS 工具">
      <button v-for="tab in subTabs" :key="tab.key" :class="['sub-tab', { active: activeSubTab === tab.key }]" :aria-pressed="activeSubTab === tab.key" @click="activeSubTab = tab.key">
        {{ tab.label }}
        <span v-if="tab.badge" class="sub-badge">{{ tab.badge }}</span>
      </button>
    </nav>

    <!-- 搜索 -->
    <div v-show="activeSubTab === 'search'" class="sub-content">
      <div class="search-form">
        <input v-model="searchForm.keyword" aria-label="岗位搜索关键词" placeholder="搜索关键词（如 Python 开发）" class="input search-keyword" @keyup.enter="searchJobs" />
        <select v-model="searchForm.city" aria-label="城市" class="input input-sm">
          <option value="">全国</option>
          <option v-for="c in cities" :key="c" :value="c">{{ c }}</option>
        </select>
        <select v-model="searchForm.experience" class="input input-sm" title="工作经验">
          <option value="">经验不限</option>
          <option v-for="o in filterOptions.experience" :key="o" :value="o">{{ o }}</option>
        </select>
        <select v-model="searchForm.salary" class="input input-sm" title="薪资范围">
          <option value="">薪资不限</option>
          <option v-for="o in filterOptions.salary" :key="o" :value="o">{{ o }}</option>
        </select>
        <select v-model="searchForm.degree" class="input input-sm" title="学历要求">
          <option value="">学历不限</option>
          <option v-for="o in filterOptions.degree" :key="o" :value="o">{{ o }}</option>
        </select>
        <select v-model="searchForm.scale" class="input input-sm" title="公司规模">
          <option value="">规模不限</option>
          <option v-for="o in filterOptions.scale" :key="o" :value="o">{{ o }}</option>
        </select>
        <select v-model="searchForm.job_type" class="input input-sm" title="职位类型">
          <option value="">类型不限</option>
          <option v-for="o in filterOptions.jobType" :key="o" :value="o">{{ o }}</option>
        </select>
        <div class="search-actions">
          <button class="btn btn-primary" @click="searchJobs" :disabled="loading || !status.browser_running">
            {{ loading ? '搜索中...' : '搜索' }}
          </button>
          <button class="btn btn-secondary" @click="scanPage" :disabled="loading || !status.browser_running">扫描当前页</button>
          <button
            class="btn btn-success"
            @click="scanAndApply"
            :disabled="loading || !status.browser_running || !pendingApplyCount"
            :title="pendingApplyCount ? `投递当前列表中 ${pendingApplyCount} 个待投递岗位` : '当前列表没有待投递的岗位'"
          >一键投递<template v-if="pendingApplyCount">（{{ pendingApplyCount }}）</template></button>
        </div>
      </div>
      <p v-if="applyFeedback" class="apply-feedback" role="status">{{ applyFeedback }}</p>
      <div class="search-result-bar" v-if="searched">
        <span>本次搜索返回 <b>{{ jobs.length }}</b> 个岗位<template v-if="jobs.length">（已保存/更新）</template></span>
        <span v-if="searchSourceText" class="search-source">{{ searchSourceText }}</span>
      </div>
      <!-- 没有搜索结果时看到的是历史记录。不标出来的话，用户会以为这些就是
           当前关键词的结果（曾把历次搜索的并集当成"搜 AI开发 出了机修工"） -->
      <div class="search-result-bar historical" v-else-if="jobs.length && listFromCache">
        <span>以下是 <b>历史岗位记录</b>（{{ jobs.length }} 条，来自历次搜索）—— 输入关键词搜索可刷新</span>
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
              {{ job.status === 'pending' ? '投递' : (job.status === 'greeting_sent' || job.status === 'applied') ? '已沟通' : job.status === 'apply_unknown' ? '待核对' : job.status }}
            </button>
            <button class="btn btn-sm btn-secondary" @click="analyzeJob(job)">AI分析</button>
            <button class="btn btn-sm btn-accent" @click="importJobToWorkflow(job)" :disabled="loading">
              导入岗位
            </button>
          </div>
        </div>
      </div>
      <div v-else class="empty-hint">
        <p v-if="searchHint" class="search-hint">{{ searchHint }}</p>
        <p v-else-if="searched">本次搜索没有返回岗位。</p>
        <p v-else>暂无岗位数据。请先启动浏览器并搜索岗位。</p>
      </div>
    </div>

    <!-- 会话 -->
    <div v-show="activeSubTab === 'conversations'" class="sub-content">
      <!-- 会话列表 -->
      <template v-if="!currentConversation">
        <div class="conv-list" v-if="conversations.length">
          <button v-for="conv in conversations" :key="conv.id" type="button" class="conv-card" :class="{ unread: conv.has_unreplied }" @click="selectConversation(conv)">
            <div class="conv-header">
              <span class="conv-name">{{ conv.hr_name }}</span>
              <span class="conv-interest" v-if="conv.interest_level" :class="'interest-' + conv.interest_level">{{ conv.interest_level }}</span>
            </div>
            <div class="conv-meta">
              <span v-if="conv.job_title">{{ conv.job_title }}</span>
              <span v-if="conv.hr_company">{{ conv.hr_company }}</span>
            </div>
            <div class="conv-last" v-if="conv.last_message_text">{{ conv.last_message_text }}</div>
          </button>
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
          <input v-model="sendMessageText" class="input" aria-label="给招聘方的消息" placeholder="输入消息..." @keyup.enter="sendConversationMessage" />
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
          <label>{{ settingsForm.apply_dynamic_enabled === 'false' ? '每日投递上限' : '每日投递起步额度' }}</label>
          <input v-model="settingsForm.daily_apply_limit" class="input input-sm" />
        </div>
        <div class="setting-row">
          <label>投递额度自适应</label>
          <select v-model="settingsForm.apply_dynamic_enabled" class="input input-sm">
            <option value="true">开启（顺利逐步加码，撞风控自动收缩）</option>
            <option value="false">关闭（固定用上面的上限）</option>
          </select>
        </div>
        <div class="setting-row" v-if="settingsForm.apply_dynamic_enabled !== 'false'">
          <label>动态额度上限</label>
          <input v-model="settingsForm.apply_daily_max" class="input input-sm" placeholder="50" />
        </div>
        <div class="setting-row">
          <label>自动回复</label>
          <select v-model="autoReplySwitch" class="input input-sm" :disabled="togglingReply">
            <option value="true">开启</option>
            <option value="false">关闭</option>
          </select>
        </div>
        <div class="setting-row">
          <label>扫描周期（秒）</label>
          <input v-model="settingsForm.scan_interval_sec" class="input input-sm" placeholder="3600" />
        </div>
        <div class="setting-row">
          <label>心跳保活（秒）</label>
          <input v-model="settingsForm.heartbeat_interval_sec" class="input input-sm" placeholder="120" />
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
          <label>追发定制话术</label>
          <select v-model="settingsForm.custom_greeting_enabled" class="input input-sm">
            <option value="true">开启</option>
            <option value="false">关闭</option>
          </select>
        </div>
        <div class="setting-row">
          <label>追发间隔（秒）</label>
          <div class="delay-range">
            <input v-model="settingsForm.custom_greeting_delay_min_sec" class="input input-sm" placeholder="4" />
            <span>~</span>
            <input v-model="settingsForm.custom_greeting_delay_max_sec" class="input input-sm" placeholder="8" />
          </div>
        </div>
        <div class="setting-row">
          <label>追发字数上限</label>
          <input v-model="settingsForm.custom_greeting_max_len" class="input input-sm" placeholder="120" />
        </div>
        <div class="setting-row">
          <label>结果相关性重排</label>
          <select v-model="settingsForm.relevance_rank_enabled" class="input input-sm">
            <option value="true">开启</option>
            <option value="false">关闭</option>
          </select>
        </div>
        <div class="setting-row">
          <label>按相关性过滤</label>
          <select v-model="settingsForm.relevance_filter_enabled" class="input input-sm">
            <option value="false">关闭（只重排，不删岗位）</option>
            <option value="true">开启（丢弃低相关岗位）</option>
          </select>
        </div>
        <div class="setting-row">
          <label>过滤阈值（分）</label>
          <input v-model="settingsForm.relevance_min_score" class="input input-sm" placeholder="3" />
        </div>
        <div class="setting-row">
          <label>AI 复核相关性</label>
          <select v-model="settingsForm.relevance_llm_enabled" class="input input-sm">
            <option value="true">开启（删除明显不相关的岗位）</option>
            <option value="false">关闭</option>
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
  bossSetAutoReply,
  bossSearchJobs, bossFilters, bossListJobs, bossApply, bossApplyBatch, bossScanPage, bossAnalyze,
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
// 上次从服务端读到的设置快照，保存时用来算差异（只提交改动过的字段）
const settingsSnapshot = ref({})
const activeSubTab = ref('search')
const searchForm = ref({ keyword: '', city: '', experience: '', salary: '', degree: '', scale: '', job_type: '' })
const filterOptions = ref({ experience: [], salary: [], degree: [], scale: [], jobType: [] })
const searched = ref(false)      // 是否已经搜过一次（用来区分"没搜过"和"搜到 0 条"）
const searchHint = ref('')       // 后端给出的"0 条原因"
const applyFeedback = ref('')
const searchMeta = ref({})       // 命中来源 / 接口错误码等诊断信息
const togglingReply = ref(false)

const cities = ['北京', '上海', '深圳', '杭州', '广州', '成都', '武汉', '南京', '西安', '长沙']

/** 统一错误文案：FastAPI 422 的 detail 是数组，直接拼接会显示成 [object Object] */
function errText(e) {
  const d = e?.response?.data?.detail
  if (Array.isArray(d)) return d.map(x => x?.msg || JSON.stringify(x)).join('；')
  return d || e?.message || '请求失败'
}

// ── 自动回复开关的状态展示 ──
// monitor_state 由后端给出单一真相：no_browser / off / paused / running
const monitorState = computed(() => status.value.monitor_state || 'no_browser')
const monitorLabel = computed(() => ({
  running: '自动回复：运行中',
  paused: '自动回复：已暂停',
  off: '自动回复：已停止',
  no_browser: '自动回复：未启动',
}[monitorState.value] || '自动回复：未启动'))
const monitorDotClass = computed(() => ({
  running: 'dot-on',
  paused: 'dot-warn',
}[monitorState.value] || 'dot-off'))

/** 设置页那颗「自动回复」下拉：写穿到后端，本地不留副本。
 *
 * 它和顶栏那颗「停止 / 恢复回复」按钮是同一个开关（后端 PUT /settings 里
 * auto_reply_enabled 这一项会转调 set_auto_reply_enabled）。以前它被当成普通
 * 表单字段存在 settingsForm 里，于是点了「停止回复」之后再进设置页点保存，
 * 会把快照里的旧值 "true" 原样写回去 —— 表现为"明明设了停止，回复却没停"。
 * 现在读写都直接对着后端状态，两边不可能再不一致。 */
const autoReplySwitch = computed({
  get: () => (status.value.auto_reply_enabled ? 'true' : 'false'),
  set: (v) => { toggleAutoReply(v === 'true') },
})

const searchSourceText = computed(() => {
  const m = searchMeta.value || {}
  if (!m.source) return ''
  const label = { xhr: '接口直取', dom: '页面解析', heuristic: '文本兜底' }[m.source] || m.source
  const parts = [`来源：${label}`]
  // 写"实际成功页数/期望页数"。只写期望值会在被风控截断时骗人
  // （曾经出现：只要到 15 条，界面却显示"4 页"）
  if (m.max_pages > 1) parts.push(`${m.pages_fetched || 0}/${m.max_pages} 页`)
  if (m.res_count) parts.push(`共 ${m.res_count} 个匹配`)
  if (m.relevance?.dropped) parts.push(`相关性过滤 -${m.relevance.dropped}`)
  if (m.relevance?.llm_dropped) parts.push(`AI 复核 -${m.relevance.llm_dropped}`)
  if (m.xhr_error) parts.push(`⚠️ ${m.xhr_error}`)
  return parts.join(' · ')
})

/** 「一键投递」的投递范围：当前列表里还没投过的岗位。
 *  与 applyJob 的按钮禁用条件（status === 'pending'）保持一致。 */
const pendingApplyCount = computed(() =>
  jobs.value.filter(j => j.status === 'pending' && j.job_url).length
)

// ── 投递额度（自适应预算）──
// `daily_limit` 现在是**今日当前**预算（会随顺利程度上涨、撞风控收缩），
// 不再是设置页那个固定值；完整明细在 status.apply_budget 里。
const budgetReason = computed(() => (status.value.apply_budget || {}).reason || '')

const applyBudgetText = computed(() => {
  const b = status.value.apply_budget || {}
  const applied = status.value.today_applications || 0
  const budget = b.budget ?? status.value.daily_limit ?? 15
  let text = `今日投递: ${applied}/${budget}`
  // 动态模式下把区间也写出来，否则用户看不懂"额度怎么自己变了"
  if (b.dynamic) text += `（动态 ${b.base}→${b.max}）`
  return text
})

const budgetTitle = computed(() => {
  const b = status.value.apply_budget || {}
  if (!b.dynamic) return '固定额度（动态预算已关闭，可在设置页开启）'
  const parts = [`起步 ${b.base}，上限 ${b.max}，当前 ${b.budget}`]
  if (b.strikes) parts.push(`今日已触发 ${b.strikes} 次风控`)
  if (b.delay_multiplier > 1) parts.push(`投递间隔已拉长 ×${b.delay_multiplier.toFixed(1)}`)
  return parts.join('；')
})

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

async function loadFilters() {
  try {
    const { data } = await bossFilters()
    filterOptions.value = {
      experience: data.experience || [],
      salary: data.salary || [],
      degree: data.degree || [],
      scale: data.scale || [],
      jobType: data.jobType || [],
    }
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
  } catch (e) { alert('启动失败: ' + (errText(e))) }
  finally { loading.value = false }
}

async function stopBrowser() {
  loading.value = true
  try {
    await bossStop()
    alert('浏览器已停止')
    await refreshStatus()
  } catch (e) { alert('停止失败: ' + (errText(e))) }
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
  } catch (e) { alert('登录失败: ' + (errText(e))) }
  finally { loading.value = false }
}

async function heartbeat() {
  try { await bossHeartbeat() } catch (e) { alert(errText(e)) }
}

/** 自动回复总开关：开后端返回的是完整 status，直接合并即可 */
async function toggleAutoReply(enabled) {
  togglingReply.value = true
  try {
    const { data } = await bossSetAutoReply(enabled)
    status.value = { ...status.value, ...data }
  } catch (e) {
    alert((enabled ? '开启' : '停止') + '自动回复失败: ' + errText(e))
  } finally {
    togglingReply.value = false
  }
}

async function searchJobs() {
  loading.value = true
  try {
    const { data } = await bossSearchJobs(
      searchForm.value.keyword, searchForm.value.city, '', 60,
      {
        experience: searchForm.value.experience ? [searchForm.value.experience] : undefined,
        salary: searchForm.value.salary ? [searchForm.value.salary] : undefined,
        degree: searchForm.value.degree ? [searchForm.value.degree] : undefined,
        company_size: searchForm.value.scale ? [searchForm.value.scale] : undefined,
        job_type: searchForm.value.job_type ? [searchForm.value.job_type] : undefined,
      },
    )
    jobs.value = data.jobs || []
    searchMeta.value = data.search_meta || {}
    searchHint.value = data.hint || ''
    searched.value = true
    listFromCache.value = false
    rememberList()
  } catch (e) { alert('搜索失败: ' + (errText(e))) }
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
    // 扫描不走筛选接口，清掉上一次搜索的诊断信息，避免误导
    searched.value = false
    searchHint.value = ''
    searchMeta.value = {}
    listFromCache.value = false
    rememberList()
  } catch (e) { alert('扫描失败: ' + (errText(e))) }
  finally { loading.value = false }
}

/** 一键投递：对**当前列表**里 status=pending 的岗位批量投递。
 *
 * 早先实现走 POST /jobs/scan-and-apply —— 让后端重新扫描「浏览器当前页」的 DOM。
 * 但 apply_to_job 会 goto 到岗位详情页且不返回，所以投过任意一个岗位之后，
 * 「当前页」就不再是搜索结果页，扫描恒为 0；浏览器刚启动时也同理（停在首页/聊天页）。
 * 现在改为把列表里的 URL 直接交给 /jobs/apply-batch，与浏览器停在哪一页无关。 */
async function scanAndApply() {
  const pending = jobs.value.filter(j => j.status === 'pending' && j.job_url)
  if (!pending.length) {
    alert('当前列表没有可投递的岗位（列表为空，或都已在沟通中）')
    return
  }
  // 后端只会投到「今日剩余额度」为止，所以确认框里要报**实际会投的数量**，
  // 不然列了 70 个、实际只投 14 个，用户会以为是坏了。
  const budget = status.value.apply_budget || {}
  const remaining = typeof budget.remaining === 'number' ? budget.remaining : null
  const willApply = remaining === null ? pending.length : Math.min(pending.length, remaining)

  if (willApply <= 0) {
    alert(
      `今日投递额度已用完（${budget.reason || '预算已用尽'}）。\n` +
      `额度每天重置；撞过风控会收缩，隔天或更克制地投会重新涨回来。`
    )
    return
  }

  let msg = `确认投递当前列表中的 ${willApply} 个岗位？`
  if (willApply < pending.length) {
    msg += `\n（列表里有 ${pending.length} 个待投，今日剩余额度 ${remaining} 个，先投前 ${willApply} 个）`
  }
  msg += `\n投递之间会按「批量间隔」放慢节奏`
  if (budget.delay_multiplier > 1) msg += `（当前因风控已拉长 ×${budget.delay_multiplier.toFixed(1)}）`
  msg += `，可能需要数分钟。`
  if (!confirm(msg)) return

  applyFeedback.value = '正在投递，请等待本批结果。'
  loading.value = true
  try {
    // 批量投递按人类节奏插入了 45-120s 间隔，很容易超过默认的 300s 超时，
    // 而请求超时并不会让后端停下 —— 那就会「界面报失败、后台其实投完了」。
    // 所以这次调用不设客户端超时。
    const { data } = await bossApplyBatch(pending.slice(0, willApply).map(j => j.job_url), '', { timeout: 0 })
    const results = data?.results || []
    const {
      applied, already_applied: alreadyApplied, unknown,
      failed, not_attempted: notRun, stop_message: stopMessage,
    } = data.summary
    // 逐条回写状态。apply_batch 中途撞风控会提前结束，所以只能按前缀对齐
    // （后端是 `for url in urls: results.append(...)`，顺序与入参一致）。
    results.forEach((r, i) => {
      if (r?.success && pending[i]) pending[i].status = 'greeting_sent'
      if (r?.outcome === 'unknown' && pending[i]) pending[i].status = 'apply_unknown'
    })
    applyFeedback.value = (
      `本批投递：确认成功 ${applied} 个` +
      (alreadyApplied ? `，此前已沟通 ${alreadyApplied} 个` : '') +
      (unknown ? `，待核对 ${unknown} 个，请先查看 BOSS 会话` : '') +
      (failed ? `，失败 ${failed} 个` : '') +
      (notRun > 0 ? `，未执行 ${notRun} 个` : '') +
      (stopMessage ? `\n${stopMessage}` : '')
    )
    await refreshStatus()
  } catch (e) { applyFeedback.value = '投递请求未完成：' + errText(e) }
  finally { loading.value = false }
}

// ── 岗位列表的来源管理 ──
// BossPanel 是 v-else-if 挂载的，**切走标签页再切回来组件会被销毁重建**，
// onMounted 会重跑。所以这里必须把"上次搜索结果"存下来：
// 否则重建后会退回 GET /api/boss/jobs —— 那接口返回的是**整个 applications 表**
// （历次搜索的并集，按 updated_at 排序），会把当前关键词的结果顶掉，
// 看起来就像"搜 AI开发 却出现了机修工学徒工操作工"（实际是别的搜索留下的）。
const LAST_SEARCH_KEY = 'boss.lastJobList'
const listFromCache = ref(false)

function rememberList() {
  try {
    sessionStorage.setItem(LAST_SEARCH_KEY, JSON.stringify({
      jobs: jobs.value,
      meta: searchMeta.value,
      hint: searchHint.value,
      searched: searched.value,
    }))
  } catch {}
}

function restoreList() {
  try {
    const raw = sessionStorage.getItem(LAST_SEARCH_KEY)
    if (!raw) return false
    const saved = JSON.parse(raw)
    if (!Array.isArray(saved?.jobs) || !saved.jobs.length) return false
    jobs.value = saved.jobs
    searchMeta.value = saved.meta || {}
    searchHint.value = saved.hint || ''
    searched.value = !!saved.searched
    return true
  } catch { return false }
}

/** 载入数据库里的历史岗位（**只在没有搜索结果可恢复时**才用）
 *
 * 不要在中途调用它来"刷新状态" —— 它返回的是整张表，会把当前搜索结果顶掉。
 */
function loadHistoricalJobs() {
  bossListJobs()
    .then(({ data }) => {
      jobs.value = data.jobs || []
      listFromCache.value = true
    })
    .catch(() => {})
}

async function applyJob(job) {
  loading.value = true
  try {
    const { data } = await bossApply(job.job_url)
    // 后端返回 HTTP 200 不等于投递成功：风控冷却、命中上限、找不到「立即沟通」按钮
    // 等都是 success=false。旧实现不看返回值就把按钮改成「已沟通」，于是"后台显示
    // 已沟通、BOSS 上其实什么都没发生"。
    if (!data?.success) {
      if (data?.outcome === 'unknown') job.status = 'apply_unknown'
      alert('投递未完成: ' + (data?.message || '未知原因'))
      return
    }
    // 成功之后还要区分「谁发的」—— 后端返回值语义：
    //   success                      = 「立即沟通」确实点下去了（平台会发出它自己的招呼语）
    //   greeting_sent                = 我们额外的定制话术也发出去了
    //   custom_greeting_need_manual  = 定制话术没跟上，需要人工补一句
    // 成功的常态不弹窗（每次投递都弹太吵），只在需要人工介入时提醒。
    if (data.custom_greeting_need_manual) {
      alert('已投递成功。\n但自定义话术未能自动发送，请到会话里手动补一句。')
    }
    job.status = 'greeting_sent'
    await refreshStatus()
  } catch (e) { alert('投递失败: ' + (errText(e))) }
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
  } catch (e) { alert('分析失败: ' + (errText(e))) }
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
    alert('导入失败: ' + (errText(e)))
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
    const s = data.settings || {}
    // 注意：auto_reply_enabled 不在这里 —— 它由 autoReplySwitch 直接读写后端，
    // 不参与表单快照。混进来就会在保存时把旧值写回去，覆盖「停止回复」的操作。
    settingsForm.value = {
      daily_apply_limit: s.daily_apply_limit || '15',
      apply_dynamic_enabled: s.apply_dynamic_enabled || 'true',
      apply_daily_max: s.apply_daily_max || '50',
      scan_interval_sec: s.scan_interval_sec || '3600',
      heartbeat_interval_sec: s.heartbeat_interval_sec || '120',
      ai_reply_style: s.ai_reply_style || 'professional',
      greeting_mode: s.greeting_mode || 'template',
      custom_greeting_enabled: s.custom_greeting_enabled || 'false',
      custom_greeting_delay_min_sec: s.custom_greeting_delay_min_sec || '4',
      custom_greeting_delay_max_sec: s.custom_greeting_delay_max_sec || '8',
      custom_greeting_max_len: s.custom_greeting_max_len || '120',
      relevance_rank_enabled: s.relevance_rank_enabled || 'true',
      relevance_filter_enabled: s.relevance_filter_enabled || 'false',
      relevance_min_score: s.relevance_min_score || '3',
      relevance_llm_enabled: s.relevance_llm_enabled || 'true',
      resume_summary: s.resume_summary || '',
    }
    settingsSnapshot.value = { ...settingsForm.value }
  } catch {}
}

async function saveSettings() {
  // 只提交改动过的字段：整表提交会用表单里的旧值覆盖掉别处（Boss 页按钮、
  // CLI、Agent）刚改过的设置，而且失败/被忽略时用户完全看不出来。
  const changed = {}
  for (const [k, v] of Object.entries(settingsForm.value)) {
    if (String(v ?? '') !== String(settingsSnapshot.value[k] ?? '')) changed[k] = v
  }
  if (!Object.keys(changed).length) {
    alert('没有需要保存的改动')
    return
  }
  loading.value = true
  try {
    const { data } = await bossUpdateSettings(changed)
    // 回读一次，确认服务端真的落库了 —— 而不是只弹一句"已保存"
    await loadSettings()
    await refreshStatus()
    const applied = data?.updated ? Object.keys(data.updated) : Object.keys(changed)
    alert(`设置已保存：${applied.join('、')}`)
  } catch (e) { alert('保存失败: ' + (errText(e))) }
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
    alert('加载会话失败: ' + (errText(e)))
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
  try {
    const { data } = await bossSendMessage(currentConversation.value.id, content)
    // 成功才清空输入框：失败时（比如后台扫描占着浏览器，后端回 409）
    // 内容留在框里，用户直接再点一次发送就行，不必重新打一遍
    sendMessageText.value = ''
    const sent = data?.sent_message
    const unconfirmed = Boolean(data?.warning)
    if (sent && sent.content) {
      // 后端把刚落库的那一行回带过来了 → 本地追加，不必再拉一次全量消息
      currentMessages.value.push(sent)
      syncConversationPreview(currentConversation.value.id, content)
    } else if (!unconfirmed) {
      // 后端没回消息体（老版本/字段缺失）时退回原来的整段重拉
      const { data: full } = await bossConversationMessages(currentConversation.value.id, 100)
      currentMessages.value = full.messages || []
    }
    if (unconfirmed) {
      // 没确认发出 → 后端也没落库，本地当然也不能凭空多一条。
      // 消息很可能其实发出去了（页面重渲染慢、选择器失配都会误报），所以不报错
      // 免得用户重发成两条，但得让他自己去浏览器里看一眼。
      alert('⚠️ ' + data.warning)
    }
  } catch (e) {
    alert('发送失败: ' + (errText(e)))
  } finally {
    sendingMsg.value = false
  }
}

/** 本地更新会话列表里的"最后一条"，免得返回列表时还显示旧内容（列表 15s 才轮询一次）。 */
function syncConversationPreview(convId, content) {
  const conv = conversations.value.find(c => c.id === convId)
  if (conv) {
    conv.last_message_text = content
    conv.last_message_from = 'me'
  }
  if (currentConversation.value?.id === convId) {
    currentConversation.value.last_message_text = content
    currentConversation.value.last_message_from = 'me'
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
    alert('Agent 执行失败: ' + (errText(e)))
  } finally {
    agentRunning.value = false
  }
}

onMounted(() => {
  refreshStatus()
  loadFilters()
  loadConversations()
  loadStats()
  loadSettings()
  // 优先恢复上次搜索结果（切标签页会重建本组件，见 LAST_SEARCH_KEY 的说明）；
  // 只有从来没搜过，才退回显示数据库里的历史岗位
  if (!restoreList()) loadHistoricalJobs()
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
.boss-panel { display: flex; flex-direction: column; gap: 12px; max-width: 64rem; margin: 0 auto; }
.boss-heading { font-size: clamp(21px, 2vw, 28px); line-height: 1.3; }

.status-bar {
  display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center;
  padding: 12px 14px; border-radius: var(--radius);
  background: var(--bg-white); border: 1px solid var(--border);
}
.status-item { display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--text-light); }
.status-item.active { color: var(--success); }
.status-dot { width: 8px; height: 8px; flex: 0 0 8px; border-radius: 50%; }
.dot-warn { background: #f59e0b; box-shadow: 0 0 6px rgba(245,158,11,.4); }

/* 浏览器线程卡死横幅：不是提示，是故障 —— 用危险色 */
.wedged-banner {
  padding: 10px 16px; border-radius: 8px; font-size: 13px; line-height: 1.6;
  color: var(--danger);
  background: rgba(220, 38, 38, 0.10);  /* 兜底：老浏览器不支持 color-mix */
  background: color-mix(in srgb, var(--danger) 10%, transparent);
  border: 1px solid rgba(220, 38, 38, 0.35);
  border: 1px solid color-mix(in srgb, var(--danger) 35%, transparent);
}
.wedged-banner b { color: var(--danger); }

.search-result-bar {
  display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 6px 12px;
  font-size: 13px; color: var(--text-light);
  padding: 6px 4px;
}
.search-result-bar b { color: var(--text); }
.search-result-bar.historical { color: var(--text-lighter); }
.search-source { font-size: 12px; color: var(--text-lighter); }
.search-hint { max-width: 560px; margin: 0 auto; line-height: 1.7; color: var(--warning); }
.dot-on { background: #22c55e; box-shadow: 0 0 6px rgba(34,197,94,.4); }
.dot-off { background: var(--text-lighter); }

.action-bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.divider { width: 1px; height: 24px; background: var(--border); margin: 0 4px; }

.btn {
  min-height: 36px;
  padding: 7px 14px; border-radius: 6px; font-size: 13px; font-weight: 500;
  border: none; cursor: pointer; transition: all .15s; white-space: nowrap;
}
.btn:disabled { opacity: .5; cursor: not-allowed; }
.btn-sm { padding: 4px 10px; font-size: 12px; }
.btn-primary { background: var(--primary); color: #fff; }
.btn-primary:hover:not(:disabled) { filter: brightness(1.1); }
.btn-secondary { background: var(--surface-dark); color: var(--text-secondary); border: 1px solid var(--border); }
.btn-secondary:hover:not(:disabled) { background: var(--border); }
.btn-success { background: var(--success); color: #fff; }
.btn-success:hover:not(:disabled) { filter: brightness(1.1); }
.btn-warning { background: var(--warning); color: #fff; }
.btn-warning:hover:not(:disabled) { filter: brightness(1.1); }
.btn-danger { background: var(--danger); color: #fff; }
.btn-danger:hover:not(:disabled) { filter: brightness(1.1); }
.btn-accent { background: var(--primary-subtle); color: var(--primary-hover); }
.btn-accent:hover:not(:disabled) { filter: brightness(1.1); }

.sub-tabs { display: flex; gap: 2px; border-bottom: 1px solid var(--border); overflow-x: auto; }
.sub-tab {
  min-height: 44px;
  padding: 6px 12px; font-size: 13px; border: none; background: none;
  cursor: pointer; color: var(--text-light); border-bottom: 2px solid transparent;
  transition: all .15s; white-space: nowrap;
}
.sub-tab:hover { color: var(--text-secondary); }
.sub-tab.active { color: var(--primary-hover); border-bottom-color: var(--primary); font-weight: 650; }
.sub-badge {
  background: var(--primary); color: #fff; font-size: 10px;
  padding: 0 6px; height: 16px; min-width: 18px; border-radius: 10px;
  display: inline-flex; align-items: center; justify-content: center; margin-left: 4px;
}

.sub-content { display: flex; flex-direction: column; gap: 10px; }

.search-form {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-white);
}
.search-keyword, .search-actions { grid-column: 1 / -1; }
.search-actions { display: flex; flex-wrap: wrap; gap: 8px; padding-top: 4px; }
.apply-feedback {
  white-space: pre-line;
  padding: 12px 14px;
  border-left: 3px solid var(--primary);
  background: var(--primary-subtle);
  color: var(--text);
  line-height: 1.55;
}
.input {
  flex: 1; min-width: 0; min-height: 36px; padding: 7px 10px; border-radius: 6px;
  border: 1px solid var(--border); font-size: 13px; background: var(--bg-white);
  color: var(--text-primary);
}
.input-sm { flex: 0 0 auto; }
.input:focus { border-color: var(--primary); }

.job-list { display: flex; flex-direction: column; gap: 8px; }
.job-card {
  padding: 12px 16px; border-radius: 8px; background: var(--bg-white);
  border: 1px solid var(--border); transition: all .15s;
}
.job-card:hover { box-shadow: 0 2px 8px rgba(0,0,0,.06); }
.job-header { display: flex; flex-wrap: wrap; gap: 4px 12px; justify-content: space-between; align-items: center; }
.job-title { font-weight: 600; font-size: 14px; color: var(--text-primary); }
.job-salary { color: var(--primary); font-weight: 600; font-size: 13px; }
.job-meta { display: flex; gap: 12px; margin-top: 4px; font-size: 12px; color: var(--text-light); flex-wrap: wrap; }
.job-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.tag { font-size: 11px; padding: 2px 8px; border-radius: 4px; }
.tag-boss { background: #fef3c7; color: #92400e; }

.agent-tools { padding: 0 12px; border: 1px solid var(--border); border-radius: var(--radius); }
.agent-tools summary { display: list-item; padding: 10px 0; cursor: pointer; color: var(--text-secondary); }
.agent-row { display: flex; gap: 8px; align-items: center; margin-bottom: 10px; }
.agent-result {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 14px;
  font-size: 12px;
  max-height: 200px;
  overflow: auto;
  color: var(--text-secondary);
  margin-bottom: 10px;
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
  text-align: left; min-width: 0;
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
.delay-range { display: flex; align-items: center; gap: 6px; }
.delay-range span { color: var(--text-light); font-size: 12px; }

.empty-hint { padding: 40px; text-align: center; color: var(--text-lighter); font-size: 13px; }

@media (max-width: 900px), (pointer: coarse) {
  .btn, .input, .agent-tools summary { min-height: 44px; }
}
@media (max-width: 480px) {
  .search-form { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .agent-row { flex-wrap: wrap; }
  .agent-row .input { flex-basis: 100%; }
  .stats-grid { gap: 6px; }
  .stat-card { padding: 12px 5px; }
  .delay-range .input { width: 0; }
  .empty-hint { padding: 24px 12px; }
}
</style>
