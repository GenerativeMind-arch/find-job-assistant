<template>
  <div class="chat-panel">
    <div class="chat-header">
      <div class="header-left">
        <span class="brand-logo">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
        </span>
        <div>
          <div class="header-title">Interview Assistant</div>
          <div class="header-subtitle">AI 求职助手</div>
        </div>
      </div>
    </div>

    <!-- 消息列表 -->
    <div class="chat-messages" ref="messagesContainer">
      <div v-if="messages.length === 0" class="chat-empty">
        <div class="empty-icon">
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>
        </div>
        <p class="empty-title">开始对话吧</p>
        <p class="empty-desc">你可以粘贴岗位描述、上传简历，我来帮你分析匹配</p>
        <div class="empty-suggestions">
          <div class="suggestion-chip" @click="$emit('send', 'AI Agent应用开发工程师 北京')">
            🔍 搜索岗位
          </div>
          <div class="suggestion-chip" @click="$emit('send', '帮我分析简历匹配度')">
            📊 匹配分析
          </div>
          <div class="suggestion-chip" @click="$emit('send', '准备面试问答')">
            💬 面试准备
          </div>
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
          <div class="typing-indicator">
            <span></span><span></span><span></span>
          </div>
        </div>
      </div>
    </div>

    <!-- 输入区 -->
    <div class="chat-input-area">
      <!-- 附件预览 -->
      <div v-if="pendingFiles.length" class="pending-files">
        <div v-for="(f, i) in pendingFiles" :key="i" class="pending-file-tag">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
          {{ f.filename }}
          <button class="remove-file" @click="removeFile(i)">×</button>
        </div>
      </div>

      <div class="input-row">
        <label class="upload-btn" title="上传附件">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
          <input
            type="file"
            multiple
            accept=".pdf,.docx,.txt,.md"
            @change="onFileSelect"
            style="display:none"
          />
        </label>
        <textarea
          ref="inputEl"
          v-model="inputText"
          placeholder="搜索岗位、粘贴 JD、上传简历..."
          rows="1"
          @keydown="onKeydown"
          @input="autoResize"
        ></textarea>
        <button class="send-btn" @click="doSend" :disabled="loading || !inputText.trim() && !pendingFiles.length">
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
})

const emit = defineEmits(['send', 'upload'])

const inputText = ref('')
const pendingFiles = ref([])
const messagesContainer = ref(null)
const inputEl = ref(null)
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
  if (e.key === 'Enter' && !e.shiftKey) {
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

/* ── Header ── */
.chat-header {
  padding: 16px 22px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.96);
}
.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}
.brand-logo {
  width: 34px;
  height: 34px;
  border-radius: 10px;
  background: linear-gradient(135deg, var(--primary), #3b82f6);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 4px 10px rgba(37, 99, 235, 0.2);
}
.header-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text);
  line-height: 1.3;
}
.header-subtitle {
  font-size: 12px;
  color: var(--text-light);
  line-height: 1.3;
}

/* ── Messages ── */
.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px 18px;
  background: linear-gradient(180deg, #ffffff 0%, var(--surface) 100%);
}

/* ── Empty State ── */
.chat-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 50px 24px;
  text-align: center;
  animation: fadeInUp 0.4s ease;
  height: 100%;
}
.empty-icon {
  width: 60px;
  height: 60px;
  border-radius: 20px;
  background: var(--primary-subtle);
  color: var(--primary);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 16px;
}
.empty-title {
  font-size: 17px;
  font-weight: 600;
  color: var(--text);
  margin-bottom: 6px;
}
.empty-desc {
  font-size: 13px;
  color: var(--text-light);
  margin-bottom: 24px;
  max-width: 280px;
  line-height: 1.5;
}
.empty-suggestions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
}
.suggestion-chip {
  padding: 8px 16px;
  border-radius: var(--radius-full);
  background: var(--bg-white);
  border: 1px solid var(--border);
  font-size: 13px;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all var(--transition);
}
.suggestion-chip:hover {
  border-color: var(--primary);
  color: var(--primary);
  background: var(--primary-subtle);
  box-shadow: var(--shadow);
  transform: translateY(-1px);
}

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

/* ── Typing Indicator ── */
.typing-indicator {
  display: flex;
  gap: 5px;
  padding: 12px 16px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
}
.typing-indicator span {
  width: 7px;
  height: 7px;
  background: var(--primary);
  border-radius: 50%;
  animation: typing 1.2s ease-in-out infinite;
}
.typing-indicator span:nth-child(2) { animation-delay: 0.2s; }
.typing-indicator span:nth-child(3) { animation-delay: 0.4s; }
@keyframes typing {
  0%, 60%, 100% { opacity: 0.25; transform: scale(0.7); }
  30% { opacity: 1; transform: scale(1); }
}

/* ── Input Area ── */
.chat-input-area {
  border-top: 1px solid var(--border);
  padding: 12px 16px 14px;
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.98);
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
  height: 40px;
  width: 40px;
  cursor: pointer;
  border: 1px solid var(--border);
  border-radius: 50%;
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
  border-radius: 20px;
  border: 1px solid var(--border);
  line-height: 1.5;
  max-height: 120px;
  background: var(--surface);
  transition: all var(--transition);
  font-size: 13.5px;
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
  min-width: 40px;
  height: 40px;
  width: 40px;
  border-radius: 50%;
  background: var(--primary);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: all var(--transition);
  box-shadow: 0 2px 8px rgba(37, 99, 235, 0.2);
}
.send-btn:hover:not(:disabled) {
  background: var(--primary-hover);
  box-shadow: 0 4px 14px rgba(37, 99, 235, 0.3);
  transform: translateY(-1px) scale(1.05);
}
.send-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
  box-shadow: none;
}
.sending-text {
  font-size: 11px;
  font-weight: 500;
}
</style>
