<template>
  <div class="result-panel">
    <div class="tab-bar">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        :class="['tab-btn', { active: activeTab === tab.key }]"
        @click="activeTab = tab.key"
      >
        <span v-if="tab.icon" class="tab-icon" v-html="tab.icon"></span>
        <span>{{ tab.label }}</span>
        <span v-if="tab.badge" class="tab-badge">{{ tab.badge }}</span>
      </button>
    </div>

    <div class="tab-content">
      <transition name="tab-fade" mode="out-in">
        <ProjectIntro v-if="activeTab === 'intro'" key="intro" />
        <JobAnalysis v-else-if="activeTab === 'job'" :job="job" :gaps="gaps" :session-id="sessionId" key="job" />
        <ResumePreview v-else-if="activeTab === 'resume'" :resume-html="resumeHtml" :session-id="sessionId" key="resume" />
        <GapsPanel v-else-if="activeTab === 'gaps'" :gaps="gaps" :questions="questions" :session-id="sessionId" key="gaps" />
        <InterviewPanel v-else-if="activeTab === 'interview'" :interview-qa="interviewQa" :session-id="sessionId" :job="job" key="interview" />
        <DebugPanel v-else-if="activeTab === 'debug'" :session-id="sessionId" :resume-content="resumeContent" :render-config="renderConfig" :triggered-agents="triggeredAgents" key="debug" />
        <BossPanel v-else-if="activeTab === 'boss'" :session-id="sessionId" @imported="onBossImported" key="boss" />
      </transition>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import ProjectIntro from './tabs/ProjectIntro.vue'
import JobAnalysis from './tabs/JobAnalysis.vue'
import ResumePreview from './tabs/ResumePreview.vue'
import GapsPanel from './tabs/GapsPanel.vue'
import InterviewPanel from './tabs/InterviewPanel.vue'
import DebugPanel from './tabs/DebugPanel.vue'
import BossPanel from './tabs/BossPanel.vue'

const props = defineProps({
  sessionId: String,
  job: Object,
  gaps: Array,
  questions: Array,
  resumeHtml: Object,
  resumeContent: Object,
  renderConfig: Object,
  interviewQa: Array,
  triggeredAgents: Array,
})

const emit = defineEmits(['imported'])

function onBossImported(payload) {
  // Boss 岗位导入工作流后，通知 App.vue 切换到对话区并自动触发分析
  emit('imported', payload)
}

/** 供父组件（App.vue）切换标签页 */
function setTab(tabKey) {
  if (typeof tabKey === 'string') activeTab.value = tabKey
}

defineExpose({ setTab })

const activeTab = ref('intro')

const tabs = computed(() => [
  { key: 'intro', label: '介绍', icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>', badge: '' },
  { key: 'job', label: '岗位', icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="7" width="20" height="14" rx="2" ry="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/></svg>', badge: props.job ? '' : '' },
  { key: 'resume', label: '简历', icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>', badge: '' },
  { key: 'gaps', label: '缺口', icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>', badge: props.questions?.length || '' },
  { key: 'interview', label: '面试', icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>', badge: '' },
  { key: 'debug', label: '调试', icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>', badge: '' },
  { key: 'boss', label: 'BOSS', icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></svg>', badge: '' },
])
</script>

<style scoped>
.result-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--bg-white);
}

/* ── Tab Bar ── */
.tab-bar {
  display: flex;
  gap: 2px;
  border-bottom: 1px solid var(--border);
  padding: 10px 16px;
  flex-shrink: 0;
  overflow-x: auto;
  background: rgba(255, 255, 255, 0.96);
  scrollbar-width: none;
}
.tab-bar::-webkit-scrollbar {
  display: none;
}
.tab-btn {
  padding: 7px 12px;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-light);
  font-size: 13px;
  white-space: nowrap;
  display: flex;
  align-items: center;
  gap: 5px;
  transition: all var(--transition);
  position: relative;
  font-weight: 450;
}
.tab-btn:hover {
  color: var(--text-secondary);
  background: var(--surface);
}
.tab-btn.active {
  color: var(--primary);
  background: var(--primary-subtle);
  font-weight: 500;
}
.tab-icon {
  display: flex;
  align-items: center;
}
.tab-badge {
  background: var(--primary);
  color: #fff;
  font-size: 10px;
  padding: 0 6px;
  height: 16px;
  min-width: 18px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 600;
}

/* ── Tab Content ── */
.tab-content {
  flex: 1;
  overflow-y: auto;
  padding: 20px 24px;
  background: var(--surface);
}

/* ── Tab Transition ── */
.tab-fade-enter-active,
.tab-fade-leave-active {
  transition: opacity 0.15s ease, transform 0.15s ease;
}
.tab-fade-enter-from {
  opacity: 0;
  transform: translateY(4px);
}
.tab-fade-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
