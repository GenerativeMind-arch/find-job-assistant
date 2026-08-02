---
name: ai-smart-exam-interview
description: This skill should be used when generating mock technical interview exams, creating interview questions, mock interviews, AI exam or test paper generation, or AI-assisted teaching and knowledge review for technical candidates.
---

# AI Smart Exam Generator - Interview Edition

## Purpose

Provide a repeatable multi-agent role-play workflow (single model session or sequential steps) to produce high-quality mock technical interview papers. Align any backend or tooling work with the existing `blog-community-backend` patterns documented below.

## When to Load This Skill

Load when the user mentions exam generation, interview questions, mock interviews, AI exam generators, test papers, or technical interview preparation that requires structured output and reviewer-quality gates.

## Project Architecture Alignment (`blog-community-backend`)

When implementing or extending server-side generation, match the existing stack and patterns.

- **Object storage:** Reuse `FileService` / `FileServiceImpl` with injected `MinioClient`; do not introduce a separate MinIO service abstraction unless the codebase already adds one. Bucket and credentials come from `minio.endpoint`, `minio.access-key`, `minio.secret-key`, `minio.bucket-name` in `application.yml` (see `MinIOConfig` for the `MinioClient` bean).
- **Chat model:** Configure under `spring.ai.dashscope` (API key `AI_API_KEY`, base URL `AI_BASE_URL`, default chat options `model` / `temperature` / `max-tokens` via env such as `AI_MODEL_PRACTICE_DEFAULT`). Do not set `base-url` to a `.../compatible-mode` root; keep the native gateway root as in existing comments in `application.yml`.
- **Agents:** Follow the same builder style as `PracticePaperAgentOrchestrator` and `PracticeGradeReactAgentRunner`: inject `@Qualifier("dashScopeChatModel") ChatModel`, use `ReactAgent.builder().name(...).model(chatModel).instruction(...).outputKey(...).enableLogging(true).build()`, override models with `DashScopeChatOptions.builder().model(...).temperature(...).maxToken(...).build()` passed to `.chatOptions(...)`, and use `ParallelAgent` / `SequentialAgent` from `spring-ai-alibaba-agent-framework` when splitting specialist work.
- **Observability:** Reuse timing hooks such as `PracticeAgentTimingModelHook` and `PracticeAgentTimingRecorder` when adding new pipeline stages so logs stay consistent.
- **Repository rules:** `.cursor/rules/` may be empty in this workspace; default to existing `com.blog` packages, Lombok, Slf4j, and the same JSON-instruction string style as practice agents.

## Core Workflow (SOP)

Execute the workflow in order. Treat **Role A** as a gate: do not draft any exam content until mandatory parameters are complete.

### Role A - Exam Director (orchestrator only)

- **Never** author questions, options, or answers; only coordinate.
- **Confirm parameters:** If anything below is missing, ask politely until complete.
  - **Topic / knowledge points** (mandatory; one or more).
  - **Difficulty** (mandatory): `easy` | `medium` | `hard`.
  - **Question quantity** (mandatory): explicit counts per type, e.g. *X* multiple-choice, *Y* short-answer.
  - **Question types** (optional): if omitted, generate **multiple-choice and short-answer** by default (the two expert roles below). If the user requests additional types later, extend the workflow in a separate pass without breaking JSON contracts for the two experts.
- **Decompose and dispatch:** After confirmation, instruct the next roles with one brief that restates topic(s), difficulty, counts, and constraints. Do not merge expert outputs until Role D completes review.

### Role B - Multiple-choice expert

- Produce **exactly** the requested count of multiple-choice items.
- Each item: **4** options labeled **A-D**, **1** correct answer, **3** plausible distractors.
- Output **only** valid JSON (no Markdown fences, no commentary). Top-level shape:

```json
{
  "questions": [
    {
      "type": "multiple_choice",
      "stem": "string",
      "options": {
        "A": "string",
        "B": "string",
        "C": "string",
        "D": "string"
      },
      "correct": "A"
    }
  ]
}
```

- Requirements: `correct` must be exactly one of `"A"`,`"B"`,`"C"`,`"D"`; options must be mutually exclusive and falsifiable; stems must read like real interview prompts.

### Role C - Short-answer expert

- Produce **exactly** the requested count of short-answer items testing depth (trade-offs, reasoning, failure modes, not trivia).
- Each item: a **detailed reference answer** and **explicit scoring points** (bullet-level granularity).
- Output **only** valid JSON. Top-level shape:

```json
{
  "questions": [
    {
      "type": "short_answer",
      "stem": "string",
      "reference_answer": "string",
      "scoring_points": ["string", "string"]
    }
  ]
}
```

### Role D - Final reviewer (interviewer)

- Act as an experienced interviewer: audit the **combined** set after B and C (and any director-approved extras).
- **Load and apply** `references/interview_rubric.md` as the scoring and quality standard.
- **Checklist:**
  - Interview realism: items should resemble what candidates face in real loops for the given topic and level.
  - Difficulty fit: easy/medium/hard must match depth, not trick wording.
  - Factual correctness: fix or remove wrong or outdated statements.
  - Clarity: remove ambiguity; ensure MC keys are unambiguous; ensure short rubrics align with stems.
- **Output policy:** Return the **final** question set as cleaned JSON **or** hand off to the assembly step that produces the Markdown paper. If revising JSON, preserve the same schemas as B/C unless a structural fix is required.

## Final Deliverable (Markdown mock paper)

After the reviewer accepts the items, emit **one** Markdown document for the candidate, with:

1. **Front matter:** Title, topics, difficulty, counts, and date if given.
2. **Section 1 - Multiple choice:** Numbered items, options A-D, and a separate **Answer key** subsection (or footnote policy - be consistent).
3. **Section 2 - Short answer:** Numbered items only (answers and rubric in an **Examiner's appendix** unless the user asked for candidate-only copy).
4. **Per-question `Interviewer's intent`:** After each question (or grouped at end per question id), state what signal the interviewer wants (knowledge vs reasoning vs communication).
5. **`Interview Scoring Rubric`:** Global table or bullets for bands (e.g. fail / pass / strong / excellent) aligned with `references/interview_rubric.md`.
6. **`Common wrong answer examples`:** For each question or grouped by topic, list typical mistakes, misconceptions, and weak patterns.

Use clear headings and numbering (e.g. `### MC-1`, `### SA-1`). Write professional, concise Markdown.

## Operational Notes

- Keep JSON from experts machine-valid: no trailing commas, no comments, UTF-8 text.
- If the user wants backend persistence to MinIO, use `FileService.uploadBytes` (or existing upload APIs) with an appropriate `bizType`; do not bypass metadata in `tb_file_resource` if the product stores files there.
- For long pipelines in Java, mirror `PracticePaperAgentOrchestrator`: parallelize independent experts when `ParallelAgent` applies (`subAgents >= 2`), otherwise run a single `ReactAgent`.

## Bundled Reference

- `references/interview_rubric.md` - General technical interview scoring standard for Role D.
