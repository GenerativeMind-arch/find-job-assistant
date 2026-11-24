<template>
  <div class="chat-panel">
    <header class="chat-header"><h2>求职对话</h2></header>

    <!-- 消息列表 -->
    <div class="chat-messages" ref="messagesContainer" role="log" aria-label="对话消息">
      <div v-if="messages.length === 0" class="chat-empty">
        <h3 class="empty-title">先从一个目标岗位开始</h3>
        <p class="empty-desc">提供岗位描述或已有简历，助手会帮你分析匹配度、完善简历并准备面试。</p>
        <div class="starter-actions">
          <button type="button" @click="inputEl?.focus()">粘贴岗位描述</button>
          <button type="button" @click="fileInput?.click()">上传简历</button>
          <button type="button" @click="emit('open-boss')">打开 BOSS 岗位</button>
        </div>
      </div>

      <div
        v-for="msg in messages"
        :key="msg.id"
        class="message-item"
        :class="msg.role"
      >
        <div class="message-avatar" :class="msg.role">
          {{ msg.role === 'user' ? 'U' : 'A' }}
        </div>
        <div class="message-body">
          <div class="message-content" v-html="renderContent(msg.content)"></div>
          <div v-if="msg.attachments?.length" class="message-attachments">
            <span v-for="(a, i) in msg.attachments" :key="i" class="attachment-tag">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
              {{ a }}
            </span>
          </div>
          <div class="message-meta">
            <span class="message-time">{{ msg.time }}</span>
          </div>
        </div>
      </div>

      <div v-if="loading" class="message-item assistant">
        <div class="message-avatar assistant">A</div>
        <div class="message-body">
          <div class="typing-indicator" role="status">
            正在处理
          </div>
        </div>
      </div>
    </div>

    <!-- 输入区 -->
    <div class="chat-input-area">
      <p v-if="notice" class="chat-notice" role="status">{{ notice }}</p>
      <!-- 附件预览 -->
      <div v-if="pendingFiles.length" class="pending-files">
        <div v-for="(f, i) in pendingFiles" :key="i" class="pending-file-tag">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
          {{ f.filename }}
          <button class="remove-file" :aria-label="'移除附件 ' + f.filename" @click="removeFile(i)">×</button>
        </div>
      </div>

      <div class="input-row">
        <button type="button" class="upload-btn" title="上传附件" aria-label="上传附件" @click="fileInput?.click()">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
        </button>
        <input
          ref="fileInput"
          type="file"
          multiple
          accept=".pdf,.docx,.txt,.md"
          hidden
          @change="onFileSelect"
        />
        <textarea
          ref="inputEl"
          v-model="inputText"
          aria-label="输入求职消息"
          placeholder="粘贴岗位描述，或输入你的问题"
          rows="1"
          @keydown="onKeydown"
          @input="autoResize"
        ></textarea>
        <button class="send-btn" aria-label="发送消息" @click="doSend" :disabled="loading || !inputText.trim() && !pendingFiles.length">
          <svg v-if="!loading" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
          <span v-else class="sending-text">发送中</span>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, nextTick } from 'vue'
import { marked } from 'marked'
import DOMPurify from '../utils/purify.js'

const props = defineProps({
  messages: Array,
  loading: Boolean,
  notice: String,
})

const emit = defineEmits(['send', 'upload', 'open-boss'])

const inputText = ref('')
const pendingFiles = ref([])
const messagesContainer = ref(null)
const inputEl = ref(null)
const fileInput = ref(null)
const renderCache = new Map()
const maxCacheSize = 120

marked.use({
  gfm: true,
  breaks: true,
})

// Auto-scroll on new messages
watch(
  () => props.messages.length,
  () => nextTick(() => scrollToBottom()),
)
watch(
  () => props.loading,
  () => nextTick(() => scrollToBottom()),
)

function scrollToBottom() {
  const el = messagesContainer.value
  if (el) el.scrollTop = el.scrollHeight
}

function renderContent(text) {
  if (!text) return ''
  const cacheKey = String(text)

  if (renderCache.has(cacheKey)) return renderCache.get(cacheKey)

  const html = marked.parse(cacheKey)
  const cleanHtml = DOMPurify(html)

  renderCache.set(cacheKey, cleanHtml)
  if (renderCache.size > maxCacheSize) {
    renderCache.delete(renderCache.keys().next().value)
  }

  return cleanHtml
}

function onKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    doSend()
  }
}

function autoResize() {
  const el = inputEl.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 120) + 'px'
}

function doSend() {
  if (props.loading) return
  const text = inputText.value.trim()

  if (pendingFiles.value.length > 0) {
    emit('upload', { text, attachments: [...pendingFiles.value] })
    pendingFiles.value = []
  } else if (text) {
    emit('send', text)
  } else {
    return
  }
  inputText.value = ''
  nextTick(() => autoResize())
}

function onFileSelect(e) {
  const files = Array.from(e.target.files)
  for (const file of files) {
    const reader = new FileReader()
    reader.onload = () => {
      const base64 = reader.result.split(',')[1]
      pendingFiles.value.push({
        filename: file.name,
        content: base64,
      })
    }
    reader.readAsDataURL(file)
  }
  e.target.value = ''
}

function removeFile(index) {
  pendingFiles.value.splice(index, 1)
}
</script>

<style scoped>
.chat-panel {
  width: 100%;
  min-width: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--bg-white);
}

.chat-header {
  display: flex;
  align-items: center;
  min-height: 60px;
  padding: 12px clamp(16px, 2vw, 26px);
  border-bottom: 1px solid var(--border);
}
.chat-header h2 { font-size: 15px; font-weight: 650; }

.chat-messages {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 18px clamp(16px, 2vw, 28px);
}
.chat-empty {
  min-height: 100%;
  max-width: 32rem;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 16px 4px 36px;
}
.empty-title {
  font-size: clamp(23px, 2.2vw, 34px);
  font-weight: 720;
  line-height: 1.28;
  letter-spacing: -0.035em;
}
.empty-desc {
  margin-top: 12px;
  max-width: 29ch;
  color: var(--text-secondary);
  font-size: 15px;
  line-height: 1.7;
}
.starter-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 22px;
}
.starter-actions button {
  min-height: 44px;
  padding: 8px 12px;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  font-weight: 550;
  transition: background var(--transition), border-color var(--transition);
}
.starter-actions button:first-child {
  background: var(--primary-subtle);
  color: var(--primary-hover);
  border-color: var(--primary-soft);
}
.starter-actions button:hover { background: var(--primary-subtle); border-color: var(--primary); }
/* ── Message Items ── */
.message-item {
  display: flex;
  gap: 10px;
  margin-bottom: 18px;
  animation: fadeInUp 0.25s ease both;
}
.message-item.user {
  flex-direction: row-reverse;
}
.message-avatar {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 700;
  flex-shrink: 0;
  letter-spacing: 0;
}
.message-avatar.assistant {
  background: var(--primary-soft);
  color: var(--primary);
}
.message-avatar.user {
  background: var(--primary);
  color: #fff;
}
.message-body {
  max-width: 80%;
}
.message-content {
  background: #fff;
  border: 1px solid var(--border);
  padding: 10px 14px;
  border-radius: var(--radius);
  line-height: 1.6;
  word-break: break-word;
  box-shadow: var(--shadow);
  font-size: 13.5px;
}
.message-content :deep(*) {
  max-width: 100%;
}
.message-content :deep(p) {
  margin: 0 0 8px;
}
.message-content :deep(p:last-child),
.message-content :deep(ul:last-child),
.message-content :deep(ol:last-child),
.message-content :deep(pre:last-child),
.message-content :deep(blockquote:last-child),
.message-content :deep(table:last-child) {
  margin-bottom: 0;
}
.message-content :deep(h1),
.message-content :deep(h2),
.message-content :deep(h3),
.message-content :deep(h4),
.message-content :deep(h5),
.message-content :deep(h6) {
  margin: 12px 0 8px;
  color: var(--text);
  font-weight: 700;
  line-height: 1.35;
}
.message-content :deep(h1) { font-size: 20px; }
.message-content :deep(h2) { font-size: 18px; }
.message-content :deep(h3) { font-size: 16px; }
.message-content :deep(h4),
.message-content :deep(h5),
.message-content :deep(h6) { font-size: 14px; }
.message-content :deep(ul),
.message-content :deep(ol) {
  margin: 0 0 10px;
  padding-left: 20px;
}
.message-content :deep(li) {
  margin: 3px 0;
}
.message-content :deep(a) {
  color: var(--primary);
  font-weight: 600;
  text-decoration: none;
}
.message-content :deep(a:hover) {
  text-decoration: underline;
}
.message-content :deep(blockquote) {
  margin: 10px 0;
  padding: 8px 12px;
  border-left: 3px solid var(--primary);
  border-radius: 0 var(--radius) var(--radius) 0;
  background: var(--primary-subtle);
  color: var(--text-secondary);
}
.message-content :deep(code) {
  padding: 2px 5px;
  border-radius: 5px;
  background: var(--primary-soft);
  color: var(--primary-hover);
  font-family: ui-monospace, SFMono-Regular, Consolas, 'Liberation Mono', monospace;
  font-size: 0.92em;
  word-break: break-word;
}
.message-content :deep(pre) {
  margin: 10px 0;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: #f7faff;
  overflow-x: auto;
  white-space: pre;
}
.message-content :deep(pre code) {
  display: block;
  min-width: max-content;
  padding: 0;
  background: transparent;
  color: var(--text);
  line-height: 1.55;
  word-break: normal;
}
.message-content :deep(table) {
  display: block;
  width: 100%;
  margin: 10px 0;
  border-collapse: collapse;
  overflow-x: auto;
  white-space: nowrap;
}
.message-content :deep(th),
.message-content :deep(td) {
  border: 1px solid var(--border);
  padding: 6px 8px;
  text-align: left;
}
.message-content :deep(th) {
  background: var(--primary-subtle);
  color: var(--text);
  font-weight: 600;
}
.message-content :deep(hr) {
  height: 1px;
  margin: 12px 0;
  border: 0;
  background: var(--border);
}
.message-item.user .message-content {
  background: var(--primary);
  border-color: var(--primary);
  color: #fff;
}
.message-item.user .message-content :deep(h1),
.message-item.user .message-content :deep(h2),
.message-item.user .message-content :deep(h3),
.message-item.user .message-content :deep(h4),
.message-item.user .message-content :deep(h5),
.message-item.user .message-content :deep(h6),
.message-item.user .message-content :deep(a) {
  color: #fff;
}
.message-item.user .message-content :deep(blockquote) {
  border-left-color: rgba(255, 255, 255, 0.78);
  background: rgba(255, 255, 255, 0.12);
  color: rgba(255, 255, 255, 0.92);
}
.message-item.user .message-content :deep(code) {
  background: rgba(255, 255, 255, 0.16);
  color: #fff;
}
.message-item.user .message-content :deep(pre) {
  border-color: rgba(255, 255, 255, 0.22);
  background: rgba(0, 0, 0, 0.12);
}
.message-item.user .message-content :deep(pre code) {
  color: #fff;
}
.message-item.user .message-content :deep(th) {
  background: rgba(255, 255, 255, 0.16);
  color: #fff;
}
.message-item.user .message-content :deep(th),
.message-item.user .message-content :deep(td) {
  border-color: rgba(255, 255, 255, 0.24);
}

/* ── Attachments ── */
.message-attachments {
  margin-top: 6px;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.attachment-tag {
  font-size: 11px;
  background: var(--primary-subtle);
  border: 1px solid var(--primary-soft);
  color: var(--primary);
  padding: 3px 8px;
  border-radius: var(--radius-sm);
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.message-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
}
.message-time {
  font-size: 11px;
  color: var(--text-light);
}
.message-item.user .message-meta {
  justify-content: flex-end;
}

.typing-indicator {
  padding: 10px 14px;
  border-radius: var(--radius);
  color: var(--primary-hover);
  background: var(--primary-subtle);
  font-size: 13px;
}
/* ── Input Area ── */
.chat-input-area {
  border-top: 1px solid var(--border);
  padding: 12px 16px 14px;
  flex-shrink: 0;
  background: var(--bg-white);
}
.chat-notice {
  margin-bottom: 8px;
  padding: 10px 12px;
  border-left: 3px solid var(--primary);
  background: var(--primary-subtle);
  color: var(--text-secondary);
  line-height: 1.5;
}
.pending-files {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}
.pending-file-tag {
  font-size: 12px;
  background: var(--primary-subtle);
  border: 1px solid var(--primary-soft);
  color: var(--primary);
  padding: 4px 10px;
  border-radius: var(--radius-sm);
  display: flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  overflow-wrap: anywhere;
}
.remove-file {
  background: none;
  color: var(--text-light);
  font-size: 15px;
  padding: 0 2px;
  line-height: 1;
  transition: color var(--transition);
}
.remove-file:hover {
  color: var(--danger);
}
.input-row {
  display: flex;
  align-items: flex-end;
  gap: 8px;
}
.upload-btn {
  height: 44px;
  width: 44px;
  cursor: pointer;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text-light);
  background: var(--bg-white);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: all var(--transition);
}
.upload-btn:hover {
  color: var(--primary);
  border-color: var(--primary);
  background: var(--primary-subtle);
}
.input-row textarea {
  flex: 1;
  resize: none;
  min-width: 0;
  padding: 10px 16px;
  border-radius: var(--radius);
  border: 1px solid var(--border);
  line-height: 1.5;
  max-height: 120px;
  background: var(--surface);
  transition: all var(--transition);
  font-size: 16px;
}
.input-row textarea:focus {
  border-color: var(--primary);
  background: #fff;
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1);
}
.input-row textarea::placeholder {
  color: var(--text-light);
}
.send-btn {
  min-width: 44px;
  height: 44px;
  padding: 0 10px;
  border-radius: var(--radius);
  background: var(--primary);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: all var(--transition);
}
.send-btn:hover:not(:disabled) {
  background: var(--primary-hover);
}
.send-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
  box-shadow: none;
}
.sending-text {
  font-size: 12px;
  font-weight: 500;
}
@media (max-width: 600px) {
  .chat-header { min-height: 48px; }
  .chat-input-area { padding: 10px; }
  .chat-empty { padding-bottom: 16px; }
}
</style>
