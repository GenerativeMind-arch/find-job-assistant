<template>
  <div class="app-layout">
    <header class="workspace-header">
      <div class="workspace-brand">
        <span class="workspace-mark" aria-hidden="true">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="7" width="16" height="13" rx="3"/><path d="M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2M4 12a20 20 0 0 0 16 0M12 12v3"/></svg>
        </span>
        <h1>求职助手</h1>
      </div>
      <nav class="mobile-tabs" aria-label="面板切换">
        <button
          type="button"
          class="mobile-tab"
          :class="{ active: activePanel === 'chat' }"
          :aria-pressed="activePanel === 'chat'"
          @click="activePanel = 'chat'"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
          对话
        </button>
        <button
          type="button"
          class="mobile-tab"
          :class="{ active: activePanel === 'result' }"
          :aria-pressed="activePanel === 'result'"
          @click="activePanel = 'result'"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>
          工作区
        </button>
      </nav>
    </header>

    <main class="workspace-body" ref="workspaceRef" :style="{ '--chat-width': chatWidth + '%' }">
      <!-- Left Panel: Chat -->
      <div
        class="layout-pane chat-pane"
        :class="{ 'is-hidden-mobile': activePanel !== 'chat' }"
      >
        <ChatPanel
          :messages="messages"
          :loading="loading"
          :notice="notice"
          @send="handleSend"
          @upload="handleUpload"
          @open-boss="openBoss"
        />
      </div>

      <div
        class="divider-handle"
        role="separator"
        aria-label="调整对话面板宽度"
        aria-orientation="vertical"
        :aria-valuenow="chatWidth"
        aria-valuemin="30"
        aria-valuemax="68"
        tabindex="0"
        @mousedown.prevent="startResize"
        @keydown.left.prevent="resizeTo(chatWidth - 3)"
        @keydown.right.prevent="resizeTo(chatWidth + 3)"
      ></div>

      <!-- Right Panel: Results -->
      <div
        class="layout-pane result-pane"
        :class="{ 'is-hidden-mobile': activePanel !== 'result' }"
      >
        <ResultPanel
          ref="resultPanelRef"
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
          @resume-rendered="handleResumeRendered"
        />
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, nextTick, onMounted, onUnmounted } from 'vue'
import ChatPanel from './components/ChatPanel.vue'
import ResultPanel from './components/ResultPanel.vue'
import { sendChat } from './api/index.js'

const sessionId = ref('')
const loading = ref(false)
const activePanel = ref('chat')
const messages = ref([])
const workspaceRef = ref(null)
const resultPanelRef = ref(null)
const notice = ref('')

// Resizable panels
const chatWidth = ref(43)
const isResizing = ref(false)

function startResize() {
  isResizing.value = true
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
}

function resizeTo(width) {
  chatWidth.value = Math.round(Math.max(30, Math.min(68, width)))
}

function onMouseMove(e) {
  if (!isResizing.value) return
  const bounds = workspaceRef.value.getBoundingClientRect()
  resizeTo((e.clientX - bounds.left) / bounds.width * 100)
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
  onMouseUp()
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
  notice.value = ''
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
  notice.value = ''
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
  notice.value = data.notice || ''
  if (data.job) job.value = data.job
  if (data.gaps) gaps.value = data.gaps
  if (data.questions_to_ask) questionsToAsk.value = data.questions_to_ask
  if (data.resume_content_json) resumeContentJson.value = data.resume_content_json
  if (data.render_config) renderConfig.value = data.render_config
  if (data.resume_html) resumeHtml.value = data.resume_html
  if (data.interview_qa) interviewQa.value = data.interview_qa
  if (data.triggered_agents) triggeredAgents.value = data.triggered_agents

  // 模拟面试触发：自动切换到「面试」标签页
  if (data.triggered_agents?.includes('interview_trigger')) {
    activePanel.value = 'result'
    nextTick(() => {
      resultPanelRef.value?.setTab('interview')
    })
  }
}

function openBoss() {
  activePanel.value = 'result'
  resultPanelRef.value?.setTab('boss')
}

/** 简历渲染调整后更新 resume_html / render_config */
function handleResumeRendered({ resume_html, render_config }) {
  if (resume_html) resumeHtml.value = resume_html
  if (render_config) renderConfig.value = render_config
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
  flex-direction: column;
  height: 100dvh;
  background: var(--bg);
}
.workspace-header {
  min-height: 64px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px clamp(12px, 2.5vw, 32px);
  background: var(--bg-white);
  border-bottom: 1px solid var(--border);
}
.workspace-brand {
  display: flex;
  align-items: center;
  gap: 10px;
}
.workspace-brand h1 {
  font-size: 17px;
  font-weight: 680;
  white-space: nowrap;
}
.workspace-mark {
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  border-radius: 10px;
  background: var(--primary);
  color: white;
}
.workspace-body {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, var(--chat-width)) 12px minmax(0, 1fr);
  padding: 12px clamp(12px, 2.5vw, 32px) 16px;
}
.layout-pane {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--bg-white);
}
.divider-handle {
  display: grid;
  place-items: center;
  cursor: col-resize;
}
.divider-handle::before {
  content: '';
  width: 3px;
  height: 28px;
  border-radius: var(--radius-full);
  background: var(--border-strong);
}
.divider-handle:is(:hover, :focus-visible)::before { background: var(--primary); }
.mobile-tabs { display: none; }

@media (max-width: 900px) {
  .workspace-header { min-height: 58px; gap: 8px; }
  .workspace-brand { gap: 8px; }
  .workspace-brand h1 { font-size: 15px; }
  .workspace-mark { width: 32px; height: 32px; }
  .workspace-body { display: flex; padding: 8px 8px env(safe-area-inset-bottom, 0px); }
  .layout-pane { width: 100%; }
  .divider-handle, .layout-pane.is-hidden-mobile { display: none; }
  .mobile-tabs {
    display: flex;
    gap: 4px;
    padding: 3px;
    border-radius: 10px;
    background: var(--surface-dark);
  }
  .mobile-tab {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 5px;
    padding: 0 10px;
    min-height: 44px;
    border-radius: var(--radius-sm);
    color: var(--text-secondary);
    white-space: nowrap;
  }
  .mobile-tab.active {
    color: var(--primary-hover);
    background: var(--bg-white);
    font-weight: 650;
    box-shadow: var(--shadow);
  }
}
</style>
