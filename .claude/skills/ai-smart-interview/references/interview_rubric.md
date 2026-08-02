# General Technical Interview Scoring Standard

Reference for the **Final reviewer (interviewer)** role. Use this to judge realism, difficulty, signal quality, and fairness. Adapt wording to the declared role level (e.g. junior backend vs senior distributed systems).

## 1. Dimensions

| Dimension | What to look for |
|-----------|------------------|
| **Relevance** | Question targets skills that actually appear in interviews for the topic; avoids classroom trivia with no hiring signal. |
| **Clarity** | One clear ask; no hidden assumptions; defined scope (e.g. "in production", "at scale", "in a single process"). |
| **Depth vs level** | Easy: recall and basic application. Medium: trade-offs, failure modes, tooling. Hard: systems reasoning, edge cases, performance, consistency. |
| **Factual soundness** | Statements match widely accepted facts for the stack; no bogus APIs, wrong complexity claims, or deprecated patterns presented as current best practice without context. |
| **Discriminative power** | Strong candidates can shine; weak candidates expose gaps without gotcha traps or trick wording. |
| **Fairness** | No dependence on privileged trivia; avoid culture-specific riddles; keep language inclusive. |

## 2. Quality bands (holistic)

Use these as **guidance** when the reviewer comments on the whole paper or suggests replacements.

- **Below bar:** Several items are irrelevant, ambiguous, factually wrong, or trivial for the claimed level; paper would waste interview time.
- **Pass:** Most items are on-topic and clear; difficulty roughly matches; a few items may be shallow or slightly ambiguous - reviewer should fix those.
- **Strong:** Good spread of concepts; realistic scenarios; distractors (MC) are tempting for common misconceptions; short-answer rubrics reward reasoning.
- **Excellent:** Feels like a calibrated loop for the level; items separate "has read a blog" from "knows how to operate the system"; minimal edits needed.

## 3. Multiple-choice review criteria

- **Stem:** Asks for judgment, application, or diagnosis - not only definition recall if the level is medium or hard (unless topic is foundational).
- **Options:** Four parallel options, similar length and grammar where possible; exactly one best answer.
- **Distractors:** Map to plausible mistakes (wrong default, partial understanding, common misconfiguration). Remove joke or nonsense options.
- **Keys:** No "all of the above" / "none of the above" unless justified; ensure `correct` is uniquely defensible.

## 4. Short-answer review criteria

- **Stem:** Requires structured reasoning; acceptable to ask for trade-offs, design sketch, or debugging approach.
- **Reference answer:** Complete enough that two independent interviewers would score similarly; cites main concepts and consequences.
- **Scoring points:** 3-8 bullets typical; each maps to observable evidence in a candidate's answer; weights implicit in ordering or importance.
- **Anti-patterns:** Remove questions where the only correct answer is a verbatim phrase; widen stems that admit unrelated tangents.

## 5. Interview realism checklist

- Would a hiring manager recognize this as a fair 30-45 minute slice for the topic?
- Could a strong mid-level answer most "medium" items with substance, not buzzwords?
- Do "hard" items require real experience (incidents, scale, consistency) rather than obscure facts?
- Are error cases and operational concerns represented where relevant (timeouts, retries, idempotency, observability)?

## 6. Fact and ambiguity resolution

- If unsure about a technical claim, **prefer** replacing with a conservative formulation, or **remove** the item rather than shipping doubt.
- Flag **ambiguity** when multiple defensible answers exist: tighten the stem or clarify constraints (environment, scale, consistency model).
- For fast-moving stacks, prefer **timeless** concepts (invariants, trade-offs) over version-specific trivia unless the user asked for a specific version.

## 7. Difficulty calibration (quick test)

- **Easy:** Candidate with textbook knowledge and small project experience should hit most items.
- **Medium:** Candidate needs production-style judgment or systematic debugging.
- **Hard:** Candidate must integrate multiple subsystems or reason under constraints; still fair if stated in stem.

## 8. Red flags to remove or rewrite

- Trick questions whose difficulty is lexical, not technical.
- Items testing memorization of magic numbers or ultra-rare API corner cases at senior loops.
- Stereotypical puzzle questions unrelated to job skills (unless user explicitly wants them).
- Duplicate stems testing the same fact with different wording.
- **Leaky** items: correct answer visible by meta-reasoning (e.g. only one "positive-sounding" option).

---

*Use this document as a rubric for **review and revision**, not as mandatory text in the candidate-facing Markdown unless the user asks for it.*
