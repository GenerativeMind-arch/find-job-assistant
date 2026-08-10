<template>
  <section class="project-intro">
    <!-- Hero -->
    <header class="hero">
      <div class="hero-bg" aria-hidden="true">
        <span class="orb orb-1"></span>
        <span class="orb orb-2"></span>
        <span class="orb orb-3"></span>
      </div>
      <div class="hero-content">
        <span class="badge"><i class="pulse-dot"></i> AI 求职工作站</span>
        <h1>Interview Assistant</h1>
        <p class="lead">
          多智能体协同 · 岗位理解 → 简历定制 → 模拟面试 全流程一站完成。
          像和助手聊天一样，逐步补齐材料，持续打磨结果。
        </p>
        <div class="hero-links">
          <a href="https://github.com/Programmergyt/interview_assistant" target="_blank" rel="noreferrer">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22"/></svg>
            GitHub
          </a>
        </div>
      </div>
    </header>

    <!-- Pipeline -->
    <div class="pipeline" aria-label="核心流程">
      <div v-for="(step, i) in pipeline" :key="step.label" class="pipeline-step">
        <div class="step-node">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" v-html="step.icon"></svg>
        </div>
        <div class="step-label">{{ step.label }}</div>
        <div class="step-desc">{{ step.desc }}</div>
        <span v-if="i < pipeline.length - 1" class="step-connector" aria-hidden="true"></span>
      </div>
    </div>

    <!-- Cards -->
    <div class="grid">
      <article class="info-card">
        <h3>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>
          推荐使用流程
        </h3>
        <ol>
          <li>第一次对话先粘贴或上传目标 JD，让系统建立岗位上下文。</li>
          <li>继续上传个人基本信息、实习经历、项目经历或已有简历材料。</li>
          <li>信息不完整时，直接要求系统补充缺失项、追问细节或重写某段内容。</li>
          <li>生成结果后可继续下达修改指令，例如强化经历、调整语气或突出匹配点。</li>
        </ol>
      </article>

      <article class="info-card">
        <h3>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
          支持附件格式
        </h3>
        <ul>
          <li>PDF · 文本型 &amp; <span class="hl">扫描件（OCR）</span></li>
          <li>Word `.docx`</li>
          <li>Plain Text `.txt` / Markdown `.md`</li>
          <li>图片 `.png` / `.jpg` <span class="hl">（视觉识别）</span></li>
        </ul>
        <p class="tip">建议优先上传原始 JD、项目说明、实习记录和个人简历，可获得更稳定的结构化抽取结果。</p>
      </article>

      <article class="info-card wide">
        <h3>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>
          适合怎么用
        </h3>
        <p>
          准备投递时，先上传 JD，再把零散的候选人材料分批发给系统。它会在当前会话里持续记住上下文，
          并把岗位分析、简历预览、缺失信息和面试问答同步更新到右侧结果面板中。
        </p>
      </article>
    </div>
  </section>
</template>

<script setup>
const pipeline = [
  { label: 'JD 分析', desc: '解析岗位要求与技能', icon: '<rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/>' },
  { label: '画像提取', desc: '抽取候选人结构化事实', icon: '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>' },
  { label: '简历生成', desc: 'STAR 法则 · 关键词对齐', icon: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="16" y2="17"/>' },
  { label: '差距分析', desc: '技能缺口与追问', icon: '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>' },
  { label: '模拟面试', desc: '四维度评分 · 综合反馈', icon: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>' },
]
</script>

<style scoped>
.project-intro {
  display: flex;
  flex-direction: column;
  gap: 18px;
  color: var(--text);
  animation: panelFadeIn 0.24s ease both;
}

/* ── Hero ── */
.hero {
  position: relative;
  overflow: hidden;
  border-radius: calc(var(--radius) + 6px);
  background: linear-gradient(135deg, #e0f2fe 0%, #bae6fd 45%, #7dd3fc 100%);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-lg);
  padding: 36px 32px;
}
.hero-content { position: relative; z-index: 1; }

/* 科技感光球背景 */
.hero-bg { position: absolute; inset: 0; pointer-events: none; }
.hero-bg::before {
  content: '';
  position: absolute; inset: 0;
  background-image:
    linear-gradient(rgba(255, 255, 255, 0.35) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.35) 1px, transparent 1px);
  background-size: 28px 28px;
  mask-image: radial-gradient(ellipse at 30% 30%, #000 20%, transparent 75%);
  -webkit-mask-image: radial-gradient(ellipse at 30% 30%, #000 20%, transparent 75%);
}
.orb {
  position: absolute;
  border-radius: 50%;
  filter: blur(28px);
  opacity: 0.55;
}
.orb-1 { width: 180px; height: 180px; top: -60px; right: 8%; background: #38bdf8; }
.orb-2 { width: 140px; height: 140px; bottom: -50px; left: 6%; background: #0ea5e9; }
.orb-3 { width: 90px; height: 90px; top: 30%; right: 32%; background: #a5f3fc; }

.badge {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 5px 13px;
  border-radius: var(--radius-full);
  background: rgba(255, 255, 255, 0.7);
  border: 1px solid rgba(255, 255, 255, 0.9);
  font-size: 12px;
  font-weight: 700;
  color: #0369a1;
  backdrop-filter: blur(6px);
}
.pulse-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: #0ea5e9;
  box-shadow: 0 0 0 0 rgba(14, 165, 233, 0.6);
  animation: pulse 2s infinite;
}
@keyframes pulse {
  0% { box-shadow: 0 0 0 0 rgba(14, 165, 233, 0.55); }
  70% { box-shadow: 0 0 0 8px rgba(14, 165, 233, 0); }
  100% { box-shadow: 0 0 0 0 rgba(14, 165, 233, 0); }
}

.hero h1 {
  margin: 14px 0 6px;
  font-size: 32px;
  font-weight: 800;
  letter-spacing: -0.02em;
  background: linear-gradient(120deg, #075985, #0ea5e9, #075985);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}
.lead {
  margin: 0;
  max-width: 620px;
  font-size: 15px;
  line-height: 1.85;
  color: #0c4a6e;
}

.hero-links { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 20px; }
.hero-links a {
  display: inline-flex; align-items: center; gap: 7px;
  min-height: 40px; padding: 0 18px;
  border-radius: var(--radius-full);
  text-decoration: none; font-weight: 600; font-size: 14px;
  transition: transform var(--transition), box-shadow var(--transition), filter var(--transition);
}
.hero-links a {
  background: rgba(255, 255, 255, 0.75);
  border: 1px solid rgba(255, 255, 255, 0.9);
  color: #0369a1;
  backdrop-filter: blur(6px);
}
.hero-links a:hover { transform: translateY(-2px); filter: brightness(1.05); box-shadow: 0 10px 24px rgba(14, 165, 233, 0.3); }

/* ── Pipeline ── */
.pipeline {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  padding: 18px 12px;
  background: rgba(255, 255, 255, 0.75);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  backdrop-filter: blur(8px);
  box-shadow: var(--shadow);
}
.pipeline-step {
  position: relative;
  flex: 1;
  text-align: center;
  min-width: 0;
}
.step-node {
  width: 46px; height: 46px;
  margin: 0 auto 8px;
  border-radius: 14px;
  display: flex; align-items: center; justify-content: center;
  background: linear-gradient(135deg, #e0f2fe, #bae6fd);
  border: 1px solid #dbeafe;
  color: #0369a1;
  box-shadow: 0 4px 10px rgba(14, 165, 233, 0.12);
  transition: transform var(--transition), box-shadow var(--transition);
}
.pipeline-step:hover .step-node {
  transform: translateY(-3px) scale(1.04);
  box-shadow: 0 8px 18px rgba(14, 165, 233, 0.22);
}
.step-label { font-size: 13px; font-weight: 700; color: var(--text); }
.step-desc { font-size: 11px; color: var(--text-light); margin-top: 3px; line-height: 1.4; }
.step-connector {
  position: absolute;
  top: 23px; left: calc(50% + 30px);
  width: calc(100% - 60px); height: 2px;
  background: linear-gradient(90deg, #7dd3fc, #38bdf8, #7dd3fc);
  opacity: 0.5;
}
.step-connector::after {
  content: '';
  position: absolute; right: 0; top: -3px;
  width: 8px; height: 8px; border-radius: 50%;
  background: #38bdf8;
}

/* ── Cards ── */
.grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}
.info-card {
  padding: 22px;
  background: rgba(255, 255, 255, 0.85);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  backdrop-filter: blur(8px);
  transition: border-color var(--transition), box-shadow var(--transition), transform var(--transition);
}
.info-card:hover {
  border-color: var(--primary-soft);
  box-shadow: var(--shadow-md);
  transform: translateY(-1px);
}
.info-card h3 {
  margin: 0 0 12px;
  font-size: 15px;
  font-weight: 700;
  color: #0c4a6e;
  display: flex; align-items: center; gap: 8px;
}
.info-card h3 svg { color: var(--primary); }
.info-card p, .info-card li {
  color: var(--text-secondary);
  line-height: 1.85;
  font-size: 13.5px;
}
.info-card ol, .info-card ul { margin: 0; padding-left: 20px; }
.info-card li { margin-bottom: 6px; }
.hl { color: var(--primary); font-weight: 600; }
.tip {
  margin: 12px 0 0;
  font-size: 12.5px;
  color: var(--text-light);
}
.wide { grid-column: 1 / -1; }

@media (max-width: 900px) {
  .pipeline { flex-wrap: wrap; }
  .pipeline-step { flex: 1 1 45%; }
  .step-connector { display: none; }
  .grid { grid-template-columns: 1fr; }
  .wide { grid-column: auto; }
  .hero { padding: 28px 22px; }
  .hero h1 { font-size: 26px; }
}
</style>
