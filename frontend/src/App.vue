<template>
  <div class="app-layout">
    <!-- Mobile Tab Bar -->
    <div class="mobile-tabs" role="tablist" aria-label="面板切换">
      <button
        type="button"
        class="mobile-tab"
        :class="{ active: activePanel === 'chat' }"
        :aria-selected="activePanel === 'chat'"
        @click="activePanel = 'chat'"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
        对话
      </button>
      <button
        type="button"
        class="mobile-tab"
        :class="{ active: activePanel === 'result' }"
        :aria-selected="activePanel === 'result'"
        @click="activePanel = 'result'"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>
        结果
      </button>
    </div>

    <!-- Divider Handle (Desktop) -->
    <div class="divider-handle" ref="dividerRef" @mousedown="startResize">
      <div class="divider-line"></div>
    </div>

    <!-- Left Panel: Chat -->
    <div
      class="layout-pane chat-pane"
      :class="{ 'is-hidden-mobile': activePanel !== 'chat' }"
      :style="{ width: chatWidth }"
    >
      <ChatPanel
        :messages="messages"
        :loading="loading"
        @send="handleSend"
        @upload="handleUpload"
      />
    </div>

    <!-- Right Panel: Results -->
    <div
      class="layout-pane result-pane"
      :class="{ 'is-hidden-mobile': activePanel !== 'result' }"
    >
      <ResultPanel
        :session-id="sessionId"
        :job="job"
        :gaps="gaps"
        :questions="questionsToAsk"
        :resume-html="resumeHtml"
        :resume-content="resumeContentJson"
        :render-config="renderConfig"
        :interview-qa="interviewQa"
        :triggered-agents="triggeredAgents"
        @imported="handleBossImported"
      />
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import ChatPanel from './components/ChatPanel.vue'
import ResultPanel from './components/ResultPanel.vue'
import { sendChat } from './api/index.js'

const sessionId = ref('')
const loading = ref(false)
const activePanel = ref('chat')
const messages = ref([])
const dividerRef = ref(null)

// Resizable panels
const chatWidth = ref('50%')
const isResizing = ref(false)

function startResize(e) {
  isResizing.value = true
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
}

function onMouseMove(e) {
  if (!isResizing.value) return
  const pct = (e.clientX / window.innerWidth) * 100
  chatWidth.value = Math.max(30, Math.min(70, pct)) + '%'
}

function onMouseUp() {
  if (isResizing.value) {
    isResizing.value = false
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
  }
}

onMounted(() => {
  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
})
onUnmounted(() => {
  document.removeEventListener('mousemove', onMouseMove)
  document.removeEventListener('mouseup', onMouseUp)
})

// State from backend
const job = ref(null)
const gaps = ref([])
const questionsToAsk = ref([])
const resumeContentJson = ref(null)
const renderConfig = ref(null)
const resumeHtml = ref(null)
const interviewQa = ref([])
const triggeredAgents = ref([])

function addMessage(role, content, attachments = []) {
  messages.value.push({
    id: Date.now(),
    role,
    content,
    attachments,
    time: new Date().toLocaleTimeString(),
  })
}

async function handleSend(text) {
  if (!text.trim()) return
  addMessage('user', text)
  loading.value = true

  try {
    const { data } = await sendChat(sessionId.value, text)
    applyResponse(data)
    addMessage('assistant', data.reply_message || '处理完成。')
  } catch (err) {
    const msg = err.response?.data?.detail || err.message || '请求失败'
    addMessage('assistant', `请求失败：${msg}`)
  } finally {
    loading.value = false
  }
}

async function handleUpload({ text, attachments }) {
  addMessage('user', text || '（上传附件）', attachments.map(a => a.filename))
  loading.value = true

  const payloadAttachments = attachments.map(a => ({
    filename: a.filename,
    content: a.content,
    content_encoding: 'base64',
  }))

  try {
    const { data } = await sendChat(sessionId.value, text, payloadAttachments)
    applyResponse(data)
    addMessage('assistant', data.reply_message || '附件处理完成。')
  } catch (err) {
    const msg = err.response?.data?.detail || err.message || '请求失败'
    addMessage('assistant', `请求失败：${msg}`)
  } finally {
    loading.value = false
  }
}

function applyResponse(data) {
  sessionId.value = data.session_id
  if (data.job) job.value = data.job
  if (data.gaps) gaps.value = data.gaps
  if (data.questions_to_ask) questionsToAsk.value = data.questions_to_ask
  if (data.resume_content_json) resumeContentJson.value = data.resume_content_json
  if (data.render_config) renderConfig.value = data.render_config
  if (data.resume_html) resumeHtml.value = data.resume_html
  if (data.interview_qa) interviewQa.value = data.interview_qa
  if (data.triggered_agents) triggeredAgents.value = data.triggered_agents
}

/** Boss 岗位导入工作流后：切换对话面板、写入 job、自动触发分析流水线。 */
function handleBossImported({ sessionId: bossSessionId, job: bossJob, message }) {
  sessionId.value = bossSessionId || sessionId.value
  if (bossJob) job.value = bossJob
  activePanel.value = 'chat'
  if (message) addMessage('assistant', message)
  // 自动发送触发指令，走 import_boss_job 全链路（jd 已预置解析）
  handleSend('分析这个岗位')
}
</script>

<style scoped>
.app-layout {
  display: flex;
  width: 100%;
  height: 100dvh;
  overflow: hidden;
  background: var(--bg);
  position: relative;
}

/* ── Desktop Divider Handle ── */
.divider-handle {
  position: absolute;
  left: 50%;
  top: 0;
  bottom: 0;
  width: 4px;
  z-index: 10;
  cursor: col-resize;
  display: flex;
  align-items: center;
  justify-content: center;
  transform: translateX(-50%);
  transition: background var(--transition);
}
.divider-handle:hover,
.divider-handle:active {
  background: rgba(37, 99, 235, 0.08);
}
.divider-line {
  width: 2px;
  height: 40px;
  border-radius: 2px;
  background: var(--border-strong);
  transition: background var(--transition), height var(--transition);
}
.divider-handle:hover .divider-line {
  background: var(--primary);
  height: 60px;
}

/* ── Mobile Tabs ── */
.mobile-tabs {
  display: none;
}

/* ── Panels ── */
.layout-pane {
  min-width: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  animation: fadeIn 0.3s ease;
}

.chat-pane {
  flex-shrink: 0;
  border-right: 1px solid var(--border);
  z-index: 1;
}

.result-pane {
  flex: 1;
  background: var(--bg-white);
  min-width: 300px;
}

/* ── Mobile Breakpoint ── */
@media (max-width: 900px) {
  .divider-handle {
    display: none;
  }

  .app-layout {
    flex-direction: column;
  }

  .mobile-tabs {
    display: flex;
    gap: 4px;
    flex-shrink: 0;
    margin: 10px 12px;
    padding: 3px;
    border: 1px solid var(--border);
    border-radius: var(--radius-full);
    background: rgba(255, 255, 255, 0.95);
    box-shadow: var(--shadow);
    backdrop-filter: blur(12px);
  }

  .mobile-tab {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 5px;
    min-width: 0;
    padding: 8px 12px;
    border-radius: var(--radius-full);
    color: var(--text-secondary);
    font-size: 13px;
    font-weight: 500;
    transition: all var(--transition);
  }
  .mobile-tab.active {
    background: var(--primary);
    color: #fff;
    box-shadow: 0 2px 8px rgba(37, 99, 235, 0.25);
  }

  .layout-pane {
    flex: 1 1 auto;
    width: 100% !important;
    height: auto;
    min-height: 0;
  }

  .chat-pane {
    border-right: 0;
  }

  .layout-pane.is-hidden-mobile {
    display: none;
  }
}
</style>
