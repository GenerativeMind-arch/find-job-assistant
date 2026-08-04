<template>
  <div class="interview-panel">
    <!-- 未开始状态：显示开始按钮 -->
    <div v-if="phase === 'idle'" class="interview-idle">
      <div class="empty-mark" aria-hidden="true"></div>
      <h3>模拟面试</h3>
      <p>基于目标岗位和你的简历，生成 5 道针对性面试题，逐题作答并获四维度评分</p>
      <p class="hint">支持文字或语音作答 · 每轮 AI 即时评分反馈 · 结束后综合评价</p>
      <button class="primary-btn" :disabled="loading" @click="startInterview">
        {{ loading ? '准备中...' : '开始面试' }}
      </button>
      <p v-if="!hasJob" class="warn-hint">⚠️ 请先提供目标岗位（搜索或粘贴 JD）再开始面试</p>
    </div>

    <!-- 加载中 -->
    <div v-else-if="phase === 'starting'" class="interview-loading">
      <div class="spinner"></div>
      <p>正在生成面试题...</p>
    </div>

    <!-- 面试进行中 -->
    <div v-else-if="phase === 'active'" class="interview-active">
      <!-- 进度条 -->
      <div class="progress-bar">
        <div class="progress-fill" :style="{ width: progressPct + '%' }"></div>
        <span class="progress-text">{{ currentRound }}/{{ totalRounds }}</span>
      </div>

      <!-- 面试官消息（当前题） -->
      <div class="interviewer-msg">
        <div class="msg-badge">面试官</div>
        <div class="msg-content">
          <div class="msg-question">
            <span v-if="currentQuestion" class="q-category">{{ currentQuestion.category }}</span>
            <span class="q-text">{{ currentQuestion.question }}</span>
          </div>
        </div>
      </div>

      <!-- 上轮评分反馈 -->
      <div v-if="lastJudge" class="judge-feedback">
        <div class="judge-header">
          <span>第 {{ lastRound }} 题评分</span>
          <span class="judge-score">{{ lastJudge.overall_score }}/10</span>
        </div>
        <div class="dimension-scores">
          <div class="dim-item" v-for="(label, key) in dimLabels" :key="key">
            <span class="dim-label">{{ label }}</span>
            <span class="dim-bar"><i :style="{ width: (lastJudge.dimension_scores?.[key] || 0) * 10 + '%' }"></i></span>
            <span class="dim-val">{{ lastJudge.dimension_scores?.[key] || 0 }}</span>
          </div>
        </div>
        <div v-if="lastJudge.strength" class="judge-txt strong">
          <strong>✅ 优点：</strong>{{ lastJudge.strength }}
        </div>
        <div v-if="lastJudge.weakness" class="judge-txt weak">
          <strong>⚠️ 不足：</strong>{{ lastJudge.weakness }}
        </div>
        <div v-if="lastJudge.suggestion" class="judge-txt suggest">
          <strong>📚 建议：</strong>{{ lastJudge.suggestion }}
        </div>
      </div>

      <!-- 作答区 -->
      <div class="answer-area">
        <textarea
          v-model="answerText"
          :placeholder="isRecording ? '正在录音...' : '输入你的回答（或点击麦克风语音作答）'"
          rows="3"
          :disabled="loading || isRecording"
        ></textarea>
        <div class="answer-actions">
          <button class="mic-btn" :class="{ recording: isRecording }" @click="toggleRecording" :disabled="loading">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/></svg>
            <span>{{ isRecording ? '停止录音' : '语音作答' }}</span>
          </button>
          <button class="submit-btn" :disabled="loading || (!answerText.trim() && !isRecording)" @click="submitAnswer">
            {{ isLast ? '完成面试' : '提交回答' }}
          </button>
        </div>
        <p v-if="transcribeNote" class="transcribe-note">{{ transcribeNote }}</p>
      </div>
    </div>

    <!-- 结束 / 综合评价 -->
    <div v-else-if="phase === 'done'" class="interview-done">
      <h3>🎉 面试完成</h3>
      <div class="final-score">
        <span class="score-big">{{ avgScore }}</span>
        <span class="score-label">平均分 / 10</span>
      </div>

      <div v-if="strongAreas.length" class="summary-section">
        <h4>✅ 你的强项</h4>
        <div class="tag-list">
          <span v-for="s in strongAreas" :key="s" class="tag tag-strong">{{ s }}</span>
        </div>
      </div>

      <div v-if="weakAreas.length" class="summary-section">
        <h4>⚠️ 需要加强</h4>
        <div class="tag-list">
          <span v-for="w in weakAreas" :key="w" class="tag tag-weak">{{ w }}</span>
        </div>
      </div>

      <div v-if="summary" class="summary-text">
        <p>{{ summary }}</p>
      </div>

      <div v-if="recommendations.length" class="summary-section">
        <h4>📚 学习建议</h4>
        <ul class="reco-list">
          <li v-for="(r, i) in recommendations" :key="i">{{ r }}</li>
        </ul>
      </div>

      <div class="done-actions">
        <button class="primary-btn" @click="resetInterview">再来一次</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onUnmounted } from 'vue'
import {
  interviewStart, interviewChat, interviewEnd,
} from '../../api/index.js'
import { AudioRecorder } from '../../utils/recorder.js'

const props = defineProps({
  sessionId: String,
  job: Object,           // 从 ResultPanel 传入，判断是否有岗位
  interviewQa: Array,
})

const phase = ref('idle')       // idle | starting | active | done
const loading = ref(false)
const currentRound = ref(0)
const totalRounds = ref(5)
const currentQuestion = ref(null)
const answerText = ref('')
const lastJudge = ref(null)
const lastRound = ref(0)
const isLast = ref(false)
const avgScore = ref('')
const strongAreas = ref([])
const weakAreas = ref([])
const summary = ref('')
const recommendations = ref([])
const transcribeNote = ref('')

const dimLabels = { accuracy: '准确性', depth: '深度', expression: '表达', relevance: '匹配' }

const hasJob = computed(() => !!props.job)
const progressPct = computed(() => totalRounds.value ? (currentRound.value / totalRounds.value) * 100 : 0)

let recorder = null
let isRecording = false

// ── 开始面试 ──
async function startInterview() {
  if (!props.sessionId) { alert('请先在对话区发送一条消息，建立会话') ; return }
  phase.value = 'starting'
  loading.value = true
  try {
    const { data } = await interviewStart(props.sessionId)
    totalRounds.value = data.total_rounds || 5
    currentRound.value = data.round || 1
    currentQuestion.value = data.question
    isLast.value = totalRounds.value <= 1
    phase.value = 'active'
  } catch (e) {
    phase.value = 'idle'
    alert('面试启动失败：' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

// ── 提交回答 ──
async function submitAnswer() {
  if (!answerText.value.trim() && !isRecording) return
  loading.value = true
  const answer = answerText.value.trim()
  // 若正在录音，先停止
  if (isRecording) await stopRecording()

  let audioBase64 = ''
  if (recorder && recorder.hasData()) {
    try {
      audioBase64 = recorder.getWavBase64()
      transcribeNote.value = '语音已录制，正在识别...'
    } catch (e) {
      console.error('WAV 编码失败', e)
    }
  }
  answerText.value = ''

  try {
    const { data } = await interviewChat(props.sessionId, answer, audioBase64)
    lastJudge.value = data.judge
    lastRound.value = data.round
    isLast.value = data.is_last
    if (data.next_question) {
      currentQuestion.value = data.next_question
      currentRound.value = data.round
    }
    if (data.is_last) {
      // 自动结束，拉取综合报告
      await fetchSummary()
    }
    if (transcribeNote.value) transcribeNote.value = ''
  } catch (e) {
    alert('提交失败：' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

// ── 结束并获取综合评价 ──
async function fetchSummary() {
  try {
    const { data } = await interviewEnd(props.sessionId)
    avgScore.value = data.avg_score || 0
    strongAreas.value = data.strong_areas || []
    weakAreas.value = data.weak_areas || []
    summary.value = data.summary || ''
    recommendations.value = data.recommendations || []
    phase.value = 'done'
  } catch (e) {
    alert('获取综合评价失败：' + (e.response?.data?.detail || e.message))
  }
}

// ── 录音控制 ──
async function toggleRecording() {
  if (isRecording) {
    await stopRecording()
  } else {
    await startRecording()
  }
}

async function startRecording() {
  try {
    recorder = new AudioRecorder()
    await recorder.start()
    isRecording = true
    transcribeNote.value = '录音中...点击「停止录音」结束'
  } catch (e) {
    alert('无法访问麦克风：' + (e.message || '请检查浏览器权限'))
  }
}

async function stopRecording() {
  if (recorder) {
    recorder.stop()
    isRecording = false
    const hasData = recorder.hasData()
    recorder = null
    transcribeNote.value = hasData ? '录音完成，提交后自动识别' : ''
  }
}

// ── 重置 ──
function resetInterview() {
  phase.value = 'idle'
  currentQuestion.value = null
  lastJudge.value = null
  answerText.value = ''
  isLast.value = false
  avgScore.value = ''
  strongAreas.value = []
  weakAreas.value = []
  summary.value = ''
  recommendations.value = []
  currentRound.value = 0
}

onUnmounted(() => {
  if (isRecording && recorder) {
    recorder.stop()
  }
})
</script>

<style scoped>
.interview-panel {
  max-width: 720px;
  animation: panelFadeIn 0.24s ease both;
}

/* ── Idle ── */
.interview-idle {
  text-align: center;
  padding: 60px 20px;
}
.interview-idle h3 {
  font-size: 20px;
  margin: 12px 0 8px;
}
.interview-idle p {
  color: var(--text-secondary);
  font-size: 14px;
  line-height: 1.7;
}
.interview-idle .hint {
  font-size: 12px;
  color: var(--text-light);
  margin-top: 4px;
}
.empty-mark {
  width: 52px;
  height: 52px;
  margin: 0 auto;
  border-radius: 16px;
  background: var(--primary-soft);
  border: 1px solid var(--border);
  position: relative;
}
.empty-mark::before {
  content: '';
  position: absolute;
  left: 21px;
  top: 12px;
  width: 10px;
  height: 18px;
  border: 2px solid var(--primary);
  border-radius: 999px;
}
.empty-mark::after {
  content: '';
  position: absolute;
  left: 25px;
  top: 32px;
  width: 2px;
  height: 8px;
  background: var(--primary);
}
.primary-btn {
  margin-top: 20px;
  padding: 10px 28px;
  background: var(--primary);
  color: #fff;
  border: none;
  border-radius: 999px;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  transition: all var(--transition);
}
.primary-btn:hover:not(:disabled) { filter: brightness(1.1); transform: translateY(-1px); }
.primary-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.warn-hint { margin-top: 12px; color: var(--warning); font-size: 13px; }

/* ── Loading ── */
.interview-loading { text-align: center; padding: 60px; color: var(--text-secondary); }
.spinner {
  width: 32px; height: 32px;
  margin: 0 auto 16px;
  border: 3px solid var(--border);
  border-top-color: var(--primary);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* ── Active ── */
.interview-active { display: flex; flex-direction: column; gap: 16px; }
.progress-bar {
  position: relative;
  height: 8px;
  background: var(--surface-dark);
  border-radius: 999px;
  overflow: hidden;
}
.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--primary), #7c3aed);
  border-radius: 999px;
  transition: width 0.4s ease;
}
.progress-text {
  position: absolute;
  right: 0;
  top: -18px;
  font-size: 12px;
  color: var(--text-light);
}

.interviewer-msg {
  background: var(--primary-subtle);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px;
}
.msg-badge {
  display: inline-block;
  font-size: 11px;
  background: var(--primary);
  color: #fff;
  padding: 2px 10px;
  border-radius: 999px;
  margin-bottom: 8px;
}
.msg-question { display: flex; gap: 10px; align-items: flex-start; }
.q-category {
  flex-shrink: 0;
  font-size: 11px;
  background: var(--primary-soft);
  color: var(--primary);
  padding: 3px 10px;
  border-radius: 999px;
  margin-top: 2px;
}
.q-text { font-size: 15px; line-height: 1.7; font-weight: 500; }

.judge-feedback {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 14px;
  box-shadow: var(--shadow);
}
.judge-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 10px;
}
.judge-score { color: var(--primary); font-size: 16px; }
.dimension-scores { display: flex; flex-direction: column; gap: 6px; margin-bottom: 10px; }
.dim-item { display: flex; align-items: center; gap: 8px; font-size: 12px; }
.dim-label { width: 44px; color: var(--text-light); flex-shrink: 0; }
.dim-bar { flex: 1; height: 6px; background: var(--surface-dark); border-radius: 999px; overflow: hidden; }
.dim-bar i { display: block; height: 100%; background: var(--primary); border-radius: 999px; }
.dim-val { width: 24px; text-align: right; color: var(--text-secondary); }
.judge-txt { font-size: 13px; line-height: 1.7; margin-top: 6px; }
.judge-txt.strong { color: #166534; }
.judge-txt.weak { color: #991b1b; }
.judge-txt.suggest { color: #854d0e; }

.answer-area { display: flex; flex-direction: column; gap: 8px; }
.answer-area textarea {
  width: 100%;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  font-size: 14px;
  resize: vertical;
  background: #fff;
  color: var(--text);
}
.answer-area textarea:focus { outline: none; border-color: var(--primary); }
.answer-actions { display: flex; gap: 8px; justify-content: flex-end; }
.mic-btn, .submit-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  border: 1px solid var(--border);
  border-radius: 999px;
  font-size: 13px;
  cursor: pointer;
  transition: all var(--transition);
  background: #fff;
}
.mic-btn:hover:not(:disabled) { border-color: var(--primary); color: var(--primary); }
.mic-btn.recording { background: #fee2e2; border-color: #ef4444; color: #ef4444; animation: pulse 1s infinite; }
.submit-btn { background: var(--primary); color: #fff; border-color: var(--primary); }
.submit-btn:hover:not(:disabled) { filter: brightness(1.1); }
.submit-btn:disabled, .mic-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.transcribe-note { font-size: 12px; color: var(--text-light); text-align: right; }
@keyframes pulse { 50% { opacity: 0.7; } }

/* ── Done ── */
.interview-done { text-align: center; padding: 20px 10px; }
.interview-done h3 { font-size: 20px; margin-bottom: 16px; }
.final-score { margin: 16px 0; }
.score-big { font-size: 56px; font-weight: 700; color: var(--primary); }
.score-label { display: block; font-size: 13px; color: var(--text-light); margin-top: 4px; }
.summary-section { text-align: left; margin: 16px 0; }
.summary-section h4 { font-size: 14px; margin-bottom: 8px; }
.tag-list { display: flex; gap: 8px; flex-wrap: wrap; }
.tag { padding: 4px 12px; border-radius: 999px; font-size: 12px; }
.tag-strong { background: #dcfce7; color: #166534; }
.tag-weak { background: #fee2e2; color: #991b1b; }
.summary-text {
  text-align: left;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 14px;
  font-size: 14px;
  line-height: 1.8;
  margin: 16px 0;
}
.reco-list { text-align: left; padding-left: 20px; font-size: 13px; line-height: 1.8; color: var(--text); }
.done-actions { margin-top: 20px; }
</style>
