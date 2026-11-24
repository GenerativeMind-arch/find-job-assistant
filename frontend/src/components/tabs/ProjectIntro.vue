<template>
  <section class="project-intro" aria-labelledby="intro-heading">
    <header class="intro-header">
      <h2 id="intro-heading">求职工作区</h2>
      <p>岗位分析、简历和面试结果会随对话更新，可在上方切换查看。</p>
    </header>

    <section class="workflow" aria-labelledby="workflow-heading">
      <h3 id="workflow-heading">推荐顺序</h3>
      <ol>
        <li v-for="step in steps" :key="step.title">
          <div>
            <h4>{{ step.title }}</h4>
            <p>{{ step.description }}</p>
          </div>
        </li>
      </ol>
    </section>

    <footer class="intro-footer">
      <p><strong>支持的附件</strong> PDF、DOCX、TXT、Markdown</p>
      <a href="https://github.com/GenerativeMind-arch/find-job-assistant/tree/master" target="_blank" rel="noreferrer">项目源码</a>
    </footer>
  </section>
</template>

<script setup>
const steps = [
  { title: '分析目标岗位', description: '粘贴 JD 或从 BOSS 导入岗位，提取职责和关键要求。' },
  { title: '定制简历', description: '上传简历或补充项目、实习经历，按岗位要求调整内容。' },
  { title: '准备面试', description: '根据岗位和经历练习问答，查看反馈并补齐差距。' },
]
</script>

<style scoped>
.project-intro {
  display: grid;
  gap: 20px;
  max-width: 54rem;
  margin: 0 auto;
}
.intro-header {
  background: var(--primary-subtle);
  border-left: 3px solid var(--primary);
  border-radius: 0 var(--radius) var(--radius) 0;
  padding: clamp(16px, 2.7vw, 32px);
}
.intro-header h2 {
  font-size: clamp(22px, 2.3vw, 32px);
  letter-spacing: -0.035em;
  line-height: 1.25;
}
.intro-header p, .workflow p, .intro-footer {
  color: var(--text-secondary);
  font-size: 14px;
  line-height: 1.65;
}
.intro-header p { margin-top: 8px; }
.workflow {
  background: var(--bg-white);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px 20px 6px;
}
.workflow h3 { font-size: 14px; }
.workflow ol { margin: 5px 0 0 1.25rem; }
.workflow li { padding: 12px 0 12px 8px; }
.workflow li + li { border-top: 1px solid var(--border); }
.workflow li::marker { color: var(--primary); font-weight: 700; }
.workflow h4 { font-size: 15px; margin-bottom: 4px; }
.intro-footer {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 4px 16px;
}
.intro-footer strong { color: var(--text); margin-right: 5px; }
.intro-footer a {
  min-height: 44px;
  display: inline-flex;
  align-items: center;
  color: var(--primary-hover);
  text-underline-offset: 3px;
}
</style>
