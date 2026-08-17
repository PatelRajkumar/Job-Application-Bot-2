# Research: Token Efficiency in the Generator–Critic–Evaluator Loop

## Overview

This document captures research into techniques for reducing API token consumption in the iterative Generator → Critic → Evaluator loop without degrading resume quality. It covers structural inefficiencies in the current architecture, industry-standard mitigation patterns, and a prioritized implementation roadmap specific to this codebase.

Companion document: [`iterative_feedback_loop_architecture.md`](./iterative_feedback_loop_architecture.md)

---

## 1. Current Token Budget Per Iteration (Estimated)

Each full loop iteration fires **two** large LLM calls:

| Call | Model | Key Inputs Re-Sent Every Iteration | Approx. Input Tokens |
|---|---|---|---|
| `refine_resume` (Generator) | Pro | system_prompt + `master_profile_json` + `template_html` + JD + `culture_signals` + `current_html` (iter 2+) + `feedback` | ~6,000–8,000 |
| `evaluate_resume` (Evaluator) | Flash | system_prompt + `master_profile_json` + JD + `culture_signals` + stripped `current_html` | ~5,000–7,000 |

With `max_iterations = 3`, the worst-case total is **~45,000 input tokens** for the loop alone — before counting output tokens, which are priced at 4–6× the input rate on Pro.

The three biggest redundancy offenders per iteration:
1. `master_profile_json` (~9k chars) re-sent to **both** agents on **every** iteration.
2. `template_html` (~8k chars) re-sent to the Generator even in revision mode where it is irrelevant.
3. The evaluator receives stripped HTML with significant whitespace bloat left over from the tag-stripping regex.

---

## 2. Industry Patterns for Loop Token Efficiency (2024–2025)

### 2.1 Stateless Iteration with Minimal Diffs

The standard recommendation for agentic loops is to treat the LLM as a **stateless function** and have the orchestrator manage state. Only the *delta* since the last iteration — not the full history — should be sent.

In practice for document-rewriting agents this means:
- **Iteration 1 (Generation):** Send the full template + master profile. Output: a complete draft.
- **Iteration 2+ (Revision):** Send only the current draft + targeted feedback. Drop the original template; the current draft already embeds it.

This is an "artifact-based editing" pattern: the agent works on an evolving artifact rather than rebuilding from scratch each time.

### 2.2 Prompt Caching (Gemini Implicit & Explicit)

Gemini 2.5+ supports two caching tiers:

| Type | Setup | Guarantee | Savings |
|---|---|---|---|
| **Implicit** | Default-on, zero code change | None (best-effort) | ~90% on cache hits |
| **Explicit** | Upload content via cache API, pass cache ID | Guaranteed | ~90% on cached tokens |

**Key insight for hit rates:** The cache is keyed on the *prefix* of the full prompt. To maximize implicit cache hits across iterations:
- Place the largest, most static content (`master_profile_json`, rubric, persona) at the **very beginning** of the system instruction, not in the user message.
- Keep the system instruction byte-for-byte identical across all iterations within a session.

Moving `master_profile_json` from the user message into the system instruction achieves both goals simultaneously.

### 2.3 Tiered Evaluation (Fast Gate → Deep Critique)

A common pattern in production LLM pipelines is a two-stage evaluator:

1. **Cheap deterministic pre-check** (Python code, zero tokens): Verify the draft contains the top N JD keywords. If more than K are missing, synthesize the feedback directly in code and skip the LLM evaluator entirely for that iteration.
2. **LLM evaluator** (Flash model): Only invoked when the deterministic check passes, or for nuanced quality feedback the code cannot provide.

This can eliminate entire LLM evaluator calls on early, obviously-bad drafts.

### 2.4 Structured Feedback Compression (Chain of Draft)

Research from 2024 (the "Chain of Draft" paper) demonstrated that forcing a model to output reasoning in very short, structured bursts (5–15 words per step) reduces output tokens by 70–90% while maintaining accuracy on downstream tasks.

Applied to the evaluator: instead of free-form critique paragraphs, output feedback as **tagged bullet codes** — e.g., `[VERB] Bullet 2 at TechCorp: change "Maintained" → "Engineered".` This directly compresses:
- The evaluator's output tokens (cheaper).
- The feedback injected back into the generator's next-iteration prompt (smaller input on the next call).

### 2.5 Reducing Max Iterations with Better Feedback Density

A 3-iteration loop exists partly to compensate for low-quality evaluator feedback. If the evaluator outputs comprehensive, precisely targeted feedback on iteration 1, the generator can resolve all issues in a single revision pass. Published work on LLM self-refinement loops shows diminishing returns beyond 2 iterations for structured document tasks.

Reducing `max_iterations` from 3 to 2 with better feedback density eliminates ~33% of total loop calls.

---

## 3. Codebase-Specific Opportunities

### 3.1 `evaluate_resume` — HTML Stripping Leaves Whitespace Bloat

**Location:** `gemini_client.py` → `evaluate_resume()`

```python
# Current (leaves multi-space runs and blank lines)
stripped_html = re.sub(r'<[^>]+>', ' ', current_html)

# Improved (collapse whitespace after stripping)
stripped_html = re.sub(r'<[^>]+>', ' ', current_html)
stripped_html = re.sub(r'[ \t]+', ' ', stripped_html)
stripped_html = re.sub(r'\n{2,}', '\n', stripped_html).strip()
```

Estimated savings: ~15–25% fewer evaluator input tokens. Zero quality impact.

### 3.2 `master_profile_json` Re-Sent to Evaluator on Every Iteration

**Location:** `bot.py` → the generator–evaluator loop

The evaluator uses `master_profile_json` primarily for the hallucination/faithfulness check. After iteration 1, the generator has already internalized the profile. On iterations 2+, a compact skills fingerprint is sufficient for the hallucination check.

```python
# Before the loop — build a compact version once
profile = json.loads(master_profile_json)
skills_summary = (
    "Skills: " + ", ".join(profile.get("technical_skills", {}).get("languages", [])) + ". "
    "Known domains: " + ", ".join(profile.get("technical_skills", {}).get("frameworks", [])) + "."
)

# In the loop
evaluator_profile = master_profile_json if iteration == 1 else skills_summary
```

Estimated savings: ~2,000–3,000 tokens per evaluator call on iterations 2+.

### 3.3 `template_html` Re-Sent to Generator in Revision Mode

**Location:** `prompts.py` → `build_generator_prompt()`

When `current_html` and `feedback` are both set (revision mode), the `<resume_template>` block is still included in the user message. At this point the template is dead weight — the generator should revise `current_html` directly.

The fix is a conditional branch in `build_generator_prompt`: omit the `<resume_template>` block when in revision mode.

Estimated savings: ~1,500–2,000 tokens per generator call on iteration 2+.

### 3.4 JD Boilerplate Truncation Not Applied Globally

**Location:** `gemini_client.py`

`_truncate_jd_boilerplate()` is implemented and works correctly, but is only called inside `classify_company()`. It is not applied in `refine_resume()` or `evaluate_resume()`, meaning boilerplate text is re-sent to the two most expensive models in the loop.

Fix: call `jd = _truncate_jd_boilerplate(jd)` at the top of `refine_resume()` and `evaluate_resume()`.

Estimated savings: 100–500 tokens per call. Zero effort, zero risk.

### 3.5 Moving `master_profile_json` to `system_instruction` for Cache Prefix Alignment

**Location:** `prompts.py` → `build_generator_prompt()` and `build_evaluator_prompt()`

Currently, `master_profile_json` is injected into the **user message** (`contents`). The system instruction contains only the persona text and rules. Since Gemini's implicit cache is keyed on the prompt prefix, and the system instruction is always the leading prefix, moving the static profile data into the system instruction significantly increases cache hit probability across iterations within the same session.

This is especially effective because `master_profile_json` is the single largest static blob in the prompt (~9k chars / ~2,500 tokens), making it the highest-value content to cache.

---

## 4. Prioritized Implementation Roadmap

Items are ordered by effort-to-savings ratio. Items 1–5 are safe to implement together in a single session.

| Priority | Technique | File(s) | Tokens Saved / Iteration | Effort | Quality Risk |
|---|---|---|---|---|---|
| 1 | Apply JD boilerplate truncation to all calls | `gemini_client.py` | 100–500 | Trivial | None |
| 2 | Improve HTML whitespace collapse in evaluator | `gemini_client.py` | 1,000–1,500 | Low | None |
| 3 | Drop `template_html` in generator revision mode | `prompts.py` | 1,500–2,000 | Low | None |
| 4 | Skip full `master_profile_json` in evaluator iter 2+ | `bot.py` | 2,000–3,000 | Low | Very Low |
| 5 | Compress evaluator feedback to tagged bullets | `prompts.py`, `models.py` | 500–1,000 output + smaller next-iter input | Low | None — improves clarity |
| 6 | Move `master_profile_json` into `system_instruction` | `prompts.py` | ~4,000–5,000 (cached) | Medium | None |
| 7 | Deterministic keyword pre-check before evaluator | `bot.py` | ~5,000 (whole call) | Medium | Low |
| 8 | Reduce `max_iterations` 3 → 2 | `bot.py` | ~12,000–15,000 (whole round) | Low | Low (pair with #5) |
| 9 | Explicit Gemini Cache API for profile + template | `gemini_client.py` | ~4,000–5,000 guaranteed | High | None |

**Combined savings from items 1–5 alone: estimated 30–40% reduction in total loop input token cost.**

---

## 5. Open Questions — Answered

### Q1: Implicit cache reliability

**Answer: Likely near-zero hit rate in this workflow. Measurement infrastructure is also missing.**

Two problems:

1. **No visibility.** The code reads `prompt_token_count` and `candidates_token_count` from `usage_metadata` but never reads `cached_content_token_count`. The `llm_requests` table has no column for it. We are flying blind.

2. **Structural reasons for low hit rates.** Gemini implicit cache is keyed on the prompt *prefix* and is best-effort — there is no guarantee the same backend node handles consecutive calls. For this workflow:
   - Both `refine_resume` and `evaluate_resume` use stateless `generate_content()` calls, not a persistent chat. Each call is routed independently.
   - The system instruction includes a company-type-specific persona (`product_startup` / `gcc` / `it_services`) that changes the full prefix for different sessions, defeating prefix-based caching across sessions.
   - Within a single 3-iteration loop the system instruction IS identical, so iterations 2 and 3 *could* get a hit — but only if Google happens to route them to the same backend, which is not guaranteed.

**What to do:** Add a `cached_tokens INTEGER` column to the `llm_requests` table and log `getattr(response.usage_metadata, 'cached_content_token_count', 0)` in every call site. After a week of sessions, query `SELECT feature, AVG(cached_tokens::float / NULLIF(prompt_tokens,0)) FROM llm_requests GROUP BY feature` to get actual hit rates. If the result is consistently near 0, bump priority of **item 9** (explicit cache API) significantly.

---

### Q2: Skills summary accuracy

**Answer: The proposed code in the research doc has two bugs that would make it return an empty string. The correct extraction must cover all 6 skill categories.**

The research doc proposes:
```python
skills_summary = (
    "Skills: " + ", ".join(profile.get("technical_skills", {}).get("languages", [])) + ". "
    "Known domains: " + ", ".join(profile.get("technical_skills", {}).get("frameworks", [])) + "."
)
```

Bug 1 — wrong top-level key: `master_profile.json` uses `"skills"`, not `"technical_skills"`. `profile.get("technical_skills", {})` always returns `{}`, so the summary would be empty.

Bug 2 — incomplete coverage: the profile has **6** skill sub-arrays: `languages`, `databases`, `frameworks`, `cloud`, `messaging`, `tools_and_practices`. The evaluator's hallucination check needs to cover all of them. If only `languages` and `frameworks` are extracted, the evaluator cannot detect a hallucinated `AWS SageMaker` (cloud) or `RabbitMQ` (messaging) addition.

**Correct implementation:**
```python
profile = json.loads(master_profile_json)
skills = profile.get("skills", {})
all_skills = (
    skills.get("languages", []) +
    skills.get("databases", []) +
    skills.get("frameworks", []) +
    skills.get("cloud", []) +
    skills.get("messaging", []) +
    skills.get("tools_and_practices", [])
)
skills_summary = "All known skills (source of truth for hallucination check): " + ", ".join(all_skills)
```

This flattens all ~30 known skills into a single comma-separated line (~150 tokens), which is sufficient for the hallucination check on iterations 2+.

---

### Q3: Feedback compression trade-off

**Answer: There is a pre-existing bug that makes the current free-form feedback even worse than advertised. Fix the type mismatch first; tagged bullets are a net improvement with no meaningful information loss.**

The `ResumeEvaluation` model in `models.py:7` defines:
```python
feedback: str = Field(description="Specific, actionable feedback for the generator. Use newlines to separate multiple points.")
```

But `bot.py:367` consumes it as:
```python
feedback_str += "Critic Feedback to Address:\n" + "\n".join([f"- {fb}" for fb in evaluation.feedback])
```

Since `evaluation.feedback` is a `str`, `for fb in evaluation.feedback` iterates over individual **characters**. The generator receives feedback like `- C\n- R\n- I\n- T\n- I\n- C\n- A\n- L\n- :...` — essentially useless.

This bug is confirmed by the inconsistency: the evaluator prompt's example in `prompts.py:64` shows `feedback` as a JSON array, but the Pydantic model declares it as `str`. Gemini's `response_schema=ResumeEvaluation` forces the output to match the Pydantic type, so Gemini returns a newline-separated string — which then gets character-iterated.

**Fix:** Change `feedback: str` → `feedback: List[str]` in `models.py`. Update the Field description to match. This makes the existing iteration in `bot.py` work correctly.

**Tagged bullet codes (item 5) are a net improvement** over free-form string feedback for this use case. The generator already receives enough context from the full `<current_draft>` and `<master_profile>` in its prompt — it doesn't need the evaluator to re-explain the problem in prose. A compact instruction like `[VERB] TechCorp bullet 2: replace "Maintained" → "Engineered"` tells the generator *exactly what to change and where*, which is both more token-efficient and more reliably actionable than a paragraph of critique.

The only scenario where tagged bullets underperform prose: when the evaluator identifies a structural problem that requires understanding *why* something is wrong (e.g., "The summary section fails to position you as a backend specialist because..."). These cases are rare and can be handled by allowing one free-form `[CONTEXT]` tag in the bullet vocabulary.

---

### Q4: `max_iterations` reduction safety

**Answer: Safe to reduce to 2 — but only after fixing the Q3 type bug. The loop already exits early; `max_iterations=3` only matters in the failure case.**

Looking at `bot.py:362-365`, the loop has an early exit:
```python
if evaluation.passed and not evaluation.is_hallucinated:
    # exits the loop
    break
```

`max_iterations=3` is a ceiling for the unhappy path — good first drafts already use only 1 generator call + 1 evaluator call. The `evaluations` table in Supabase records `iteration_number` and `passed` per session; querying `SELECT iteration_number, COUNT(*) FROM evaluations WHERE passed = true GROUP BY iteration_number` would show exactly how often iteration 3 is reached in practice.

The risk of going 3→2 is: if the generator fails to resolve all evaluator feedback in a single revision pass, the user gets an unpolished draft with no further automated fix attempt. This risk is currently masked by the Q3 bug — because the feedback reaching the generator is character-garbage, the generator succeeds (or appears to) on iteration 2 essentially without feedback, and the evaluator then passes because some basic quality threshold was met by the initial draft.

**Decision matrix:**
| State | Safe to cut to 2 iterations? |
|---|---|
| Q3 bug NOT fixed (current state) | No — feedback is broken; iteration 3 is the only real recovery pass |
| Q3 bug fixed, Q3 tagged bullets NOT yet implemented | Yes, with monitoring — free-form `List[str]` feedback is already a large improvement |
| Q3 bug fixed + tagged bullets implemented | Yes, confidently — structured feedback is optimally dense for single-pass resolution |

**Recommended sequence:** Fix Q3 type bug → monitor pass rates via Supabase for 5–10 sessions → reduce `max_iterations` to 2 if iteration 3 pass rate is low.
