# Implementation Plan: Token Efficiency Optimizations

Companion to: [`token_efficiency_generator_critic_loop.md`](./token_efficiency_generator_critic_loop.md)

This plan converts the research roadmap into sequenced, file-level steps. Phases are ordered by dependency: each phase can only start once the previous phase is complete and verified.

---

## Agent Notes (Save Investigation Time)

> Written after Phase 0 implementation (2026-06-24). Read this before touching any phase.

### Key file map
| Concern | File | Notes |
|---|---|---|
| Pydantic models for LLM outputs | `bot/models.py` | `ResumeEvaluation`, `ResumeRevisions`, `CompanySnippet` |
| Prompt builders | `bot/prompts.py` | `build_evaluator_prompt()` line 1, `build_generator_prompt()` line 102 |
| LLM call wrappers | `bot/gemini_client.py` | `refine_resume()`, `evaluate_resume()`, `classify_company()`, `generate_cover_letter()`, `revise_resume()`, `send_message_with_retry()` |
| Generator/evaluator loop | `bot/bot.py` | `tailor_process()` — look for `max_iterations`, `for iteration in range(...)` |
| Analytics logging | `bot/analytics_logger.py` | `log_llm_request()`, `log_agent_trace()` |

### What the generator/evaluator loop looks like (bot.py)
- `tailor_process()` runs `max_iterations=3` rounds of: generate → evaluate → (if not passed) build feedback string → generate again.
- `evaluation.feedback` is iterated as a list at line ~370: `"\n".join([f"- {fb}" for fb in evaluation.feedback])` — this was always written for `List[str]`, hence the Phase 0 bug.
- `feedback_str` is passed into `refine_resume()` as the `feedback` kwarg; the generator prompt builder receives it.

### Prompt builder signatures (prompts.py)
- `build_evaluator_prompt(company_type, master_profile_json, jd, current_html, culture_signals)` → `(system_instruction, user_message)`
- `build_generator_prompt(company_type, master_profile_json, template_html, jd, culture_signals, current_html=None, feedback=None)` → `(system_instruction, user_message)`
- The evaluator's example JSON output (line ~63) already shows `feedback` as an array — consistent with the model after Phase 0.
- `_truncate_jd_boilerplate()` exists in `gemini_client.py` and is already called in `classify_company()` but NOT yet in `refine_resume()` / `evaluate_resume()` (Phase 1 work).

### HTML stripping in evaluator (gemini_client.py)
- `evaluate_resume()` now strips tags AND collapses whitespace before passing to prompt (Phase 1.2 done): `re.sub(r'<[^>]+>', ' ')` → `re.sub(r'[ \t]+', ' ')` → `re.sub(r'\n{2,}', '\n').strip()`.

### analytics_logger schema
- `llm_requests` table: `session_id, feature, model, prompt_tokens, completion_tokens, cost_usd, cached_tokens, error` — `cached_tokens` column added by Phase 1.4.
- `agent_traces` table: `session_id, iteration, agent_role, prompt_text, raw_response, parsed_output`.
- **IMPORTANT — Supabase migration required before Phase 1.4 is live:** The `CREATE TABLE IF NOT EXISTS` in `init_db()` now includes `cached_tokens INTEGER DEFAULT 0`, but existing prod tables need a one-time migration: `ALTER TABLE llm_requests ADD COLUMN IF NOT EXISTS cached_tokens INTEGER DEFAULT 0;` — run this in the Supabase SQL editor before deploying the Phase 1 code, or the INSERT will fail and all LLM request logging will break.

---

## Phase 0 — Critical Bug Fix (Prerequisite for Everything) ✅ DONE

**Must be done first. Unlocks the value of all later phases.**

The `feedback` field is typed as `str` but iterated as `List[str]` in `bot.py`, causing the generator to receive character-by-character garbage as feedback. This masks every other quality and efficiency improvement.

### Step 0.1 — Fix `ResumeEvaluation.feedback` type ✅ DONE

**File:** `bot/models.py`

Change:
```python
from pydantic import BaseModel, Field
from typing import List

class ResumeEvaluation(BaseModel):
    ...
    feedback: str = Field(description="Specific, actionable feedback for the generator. Use newlines to separate multiple points.")
```

To:
```python
class ResumeEvaluation(BaseModel):
    ...
    feedback: List[str] = Field(description="List of specific, actionable feedback points for the generator. Each item is one discrete, targeted instruction.")
```

### Step 0.2 — Update the evaluator prompt example to match ✅ DONE

**File:** `bot/prompts.py`, inside `build_evaluator_prompt()`

The example JSON block at line ~64 already shows `feedback` as an array — it's correct. No change needed there.

However, the `<task_instructions>` block at the bottom of `build_evaluator_prompt()` should reinforce that feedback is a list. Append to the existing task instructions:

```
Each feedback item must be one discrete, actionable instruction. Output 2–5 items maximum.
```

### Step 0.3 — Verify the loop consumption still works ✅ DONE (no code change needed)

**File:** `bot/bot.py`, line ~367

The existing code:
```python
feedback_str += "Critic Feedback to Address:\n" + "\n".join([f"- {fb}" for fb in evaluation.feedback])
```

This already iterates `evaluation.feedback` as a list — it was written for a `List[str]` and will work correctly once the type is fixed. No change needed to `bot.py` for this step.

### Validation
- Run one full `/tailor` session.
- Check `agent_traces` in Supabase: the `raw_response` for `agent_role = 'evaluator'` should contain a JSON array for `feedback`, not a plain string.
- Check the `agent_role = 'generator'` trace for iteration 2: `prompt_text` should show `Critic Feedback to Address:` followed by 2–5 human-readable bullet points, not individual characters.

> **Implementation note (2026-06-24):** Both code changes were minimal. `bot/models.py` line 7: `feedback: str` → `feedback: List[str]` (import `List` was already present). `bot/prompts.py` task_instructions block: appended "Each feedback item must be one discrete, actionable instruction. Output 2–5 items maximum." The example JSON at line ~66 already had `feedback` as an array so no change was needed there. `bot.py` loop at line ~370 already iterated feedback as a list, confirming the bug was purely in the Pydantic type declaration.

---

## Phase 1 — Zero-Risk Savings (Single Session) ✅ DONE

All four steps are independent and have zero quality risk. Implement together.

### Step 1.1 — Apply JD boilerplate truncation globally ✅ DONE

**File:** `bot/gemini_client.py`

`_truncate_jd_boilerplate()` is already called in `classify_company()` but not in the two most expensive calls. Add the call at the top of both methods:

In `refine_resume()` (around line 467), before `build_generator_prompt()` is called:
```python
async def refine_resume(self, jd: str, ...):
    jd = _truncate_jd_boilerplate(jd)   # ADD THIS LINE
    system_prompt, contents = build_generator_prompt(...)
```

In `evaluate_resume()` (around line 623), before `build_evaluator_prompt()` is called:
```python
async def evaluate_resume(self, current_html: str, master_profile_json: str, jd: str, ...):
    jd = _truncate_jd_boilerplate(jd)   # ADD THIS LINE
    stripped_html = re.sub(r'<[^>]+>', ' ', current_html)
    system_prompt, contents = build_evaluator_prompt(...)
```

**Estimated savings:** 100–500 tokens per call.

---

### Step 1.2 — Improve HTML whitespace collapse in evaluator ✅ DONE

**File:** `bot/gemini_client.py`, inside `evaluate_resume()` (line ~626)

Change:
```python
stripped_html = re.sub(r'<[^>]+>', ' ', current_html)
```

To:
```python
stripped_html = re.sub(r'<[^>]+>', ' ', current_html)
stripped_html = re.sub(r'[ \t]+', ' ', stripped_html)
stripped_html = re.sub(r'\n{2,}', '\n', stripped_html).strip()
```

**Estimated savings:** 1,000–1,500 tokens per evaluator call. Zero quality impact.

---

### Step 1.3 — Drop `template_html` in generator revision mode ✅ DONE

**File:** `bot/prompts.py`, inside `build_generator_prompt()`

Currently the user message always includes `<resume_template>` regardless of whether it's a revision. In revision mode (`current_html` and `feedback` are both set), the template is dead weight — the generator revises `current_html` directly.

Change the user message construction:

```python
# Replace the static user_message block with a conditional one
if current_html and feedback:
    # Revision mode: template is irrelevant, omit it
    user_message = f"""<master_profile>
{master_profile_json}
</master_profile>

<job_description>
{jd}
</job_description>

<company_culture>
{culture_signals}
</company_culture>

<current_draft>
{current_html}
</current_draft>

<feedback_to_address>
{feedback}
</feedback_to_address>

Please revise the `<current_draft>` based on the `<feedback_to_address>`. Use the `<master_profile>` as your absolute source of truth.
"""
else:
    # Initial generation: include full template
    user_message = f"""<master_profile>
{master_profile_json}
</master_profile>

<resume_template>
{template_html}
</resume_template>

<job_description>
{jd}
</job_description>

<company_culture>
{culture_signals}
</company_culture>

This is the initial draft generation. Please fill the template placeholders with the most relevant information for the job.
"""
```

**Estimated savings:** 1,500–2,000 tokens per generator call on iterations 2+.

---

### Step 1.4 — Add cached token logging ✅ DONE

**File:** `bot/analytics_logger.py` and the Supabase `llm_requests` table.

**Step 1.4a** — Add `cached_tokens` column to the table. Run this SQL in Supabase once:
```sql
ALTER TABLE llm_requests ADD COLUMN IF NOT EXISTS cached_tokens INTEGER DEFAULT 0;
```

**Step 1.4b** — Update `log_llm_request()` in `bot/analytics_logger.py` to accept and store the new field:
```python
async def log_llm_request(session_id, feature, model, prompt_tokens, completion_tokens, cost_usd, cached_tokens=0, error=None):
    ...
    await conn.execute(
        "INSERT INTO llm_requests (session_id, feature, model, prompt_tokens, completion_tokens, cost_usd, cached_tokens, error) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
        session_id, feature, model, prompt_tokens, completion_tokens, cost_usd, cached_tokens, error
    )
```

**Step 1.4c** — Read and pass `cached_content_token_count` at every call site in `bot/gemini_client.py`. In each block that reads `usage_metadata`, add:

```python
cached_tokens = getattr(response.usage_metadata, 'cached_content_token_count', 0)
```

Then pass it to `log_llm_request(..., cached_tokens=cached_tokens)`. Affects: `refine_resume()`, `evaluate_resume()`, `classify_company()`, `generate_cover_letter()`, `revise_resume()`, and `send_message_with_retry()`.

**Purpose:** After 5–10 sessions, run this Supabase query to measure implicit cache hit rate:
```sql
SELECT feature,
       ROUND(AVG(cached_tokens::numeric / NULLIF(prompt_tokens, 0)) * 100, 1) AS cache_hit_pct
FROM llm_requests
WHERE cached_tokens IS NOT NULL
GROUP BY feature
ORDER BY cache_hit_pct DESC;
```

---

### Phase 1 Validation ✅ DONE (code changes applied 2026-06-24)
- Run 2–3 full `/tailor` sessions with real JDs.
- In `llm_requests`, confirm `prompt_tokens` for `tailor_generator` and `critic_evaluator` are lower than pre-Phase 1 baseline.
- Confirm `cached_tokens` column is being populated (values may be 0 — that's valid data).
- Spot-check output quality of generated resumes is unchanged.

> **Implementation note (2026-06-24):**
> - **1.1:** Added `jd = _truncate_jd_boilerplate(jd)` as first line in both `refine_resume()` and `evaluate_resume()` in `gemini_client.py`. The helper was already implemented and used in `classify_company()` — zero new logic.
> - **1.2:** `evaluate_resume()` now runs three `re.sub` passes on `stripped_html`: tag removal → horizontal whitespace collapse → newline collapse + strip.
> - **1.3:** `build_generator_prompt()` in `prompts.py` now branches at the top of user message construction. Revision mode (`current_html and feedback` both truthy) builds a message **without** `<resume_template>` (saves ~1,500–2,000 tokens per call on iterations 2+). Initial mode still includes the full template. The `if/else` append pattern was replaced with two separate f-string assignments.
> - **1.4:** `log_llm_request()` signature changed: new `cached_tokens: int = 0` param before `error`. The INSERT now writes 8 columns. All 6 call sites in `gemini_client.py` updated to read `cached_content_token_count` from `usage_metadata` and pass `cached_tokens=`. `send_message_with_retry()` adds `cached_tokens` to its return dict so `start_chat_session()` can forward it. **Requires Supabase `ALTER TABLE` migration — see Agent Notes above.**

---

## Phase 2 — Feedback Density + Compact Profile ✅ DONE

**Depends on Phase 0 deployed and verified.**

### Step 2.1 — Structured tagged-bullet feedback format ✅ DONE

This changes the evaluator's output format to dense, targeted instructions instead of prose paragraphs.

**Step 2.1a — Update `build_evaluator_prompt()` in `bot/prompts.py`**

Replace the existing `<task_instructions>` block:
```python
<task_instructions>
Review the `<current_draft>` using the Dual-Stage Scoring Rubric. 
Think step-by-step for the Grounding Check, provide specific actionable feedback, and assign rubric scores mapping to our dual-stage rubric.
</task_instructions>
```

With:
```python
<task_instructions>
Review the `<current_draft>` using the Dual-Stage Scoring Rubric.
Think step-by-step for the Grounding Check.

For the `feedback` array, output 2–5 items max. Each item must use one of these tags:
- [VERB] <location>: replace "<old phrase>" → "<new phrase>"
- [METRIC] <location>: add or change a quantifiable metric
- [HALLUCINATION] <location>: revert — this skill is not in the master profile
- [KEYWORD] missing JD keyword "<keyword>" — add to <location>
- [CONTEXT] <free-form structural note — use only when a tag above is insufficient>

Example feedback array:
[
  "[VERB] TechCorp bullet 2: replace 'Maintained Redis cache' → 'Optimized Redis cache hit rate by 40%'",
  "[KEYWORD] missing JD keyword 'Kubernetes' — add to skills section or TechCorp bullets",
  "[HALLUCINATION] Skills section: 'AWS SageMaker' is not in master profile — revert"
]
</task_instructions>
```

**Step 2.1b — Update the example JSON output** in `build_evaluator_prompt()` to use the tagged format so Gemini sees a consistent in-context example.

**Estimated savings:** 30–50% reduction in evaluator output tokens. Also reduces the feedback payload injected into the generator's next iteration.

---

### Step 2.2 — Compact skills fingerprint for evaluator on iterations 2+ ✅ DONE

**File:** `bot/bot.py`, inside `tailor_process()`, before the `for iteration in range(...)` loop.

Add the skill extraction once, before the loop:
```python
# Build compact skills fingerprint for use in evaluator iterations 2+
import json as _json
_profile = _json.loads(master_profile_json)
_skills = _profile.get("skills", {})
_all_skills = (
    _skills.get("languages", []) +
    _skills.get("databases", []) +
    _skills.get("frameworks", []) +
    _skills.get("cloud", []) +
    _skills.get("messaging", []) +
    _skills.get("tools_and_practices", [])
)
skills_fingerprint = "All known skills (hallucination ground truth): " + ", ".join(_all_skills)
```

Then inside the loop, pass the appropriate profile to the evaluator:
```python
evaluator_profile = master_profile_json if iteration == 1 else skills_fingerprint

evaluation, eval_usage = await client.evaluate_resume(
    current_html=current_html,
    master_profile_json=evaluator_profile,   # changed
    jd=jd,
    company_type=company_type,
    culture_signals=culture_signals,
    priority=priority
)
```

**Estimated savings:** 2,000–3,000 tokens per evaluator call on iterations 2+.

---

### Phase 2 Validation
- Run 3+ sessions and inspect `agent_traces` where `agent_role = 'evaluator'`: the `raw_response` should now contain tagged feedback like `[VERB]`, `[KEYWORD]`, etc.
- Inspect `agent_role = 'generator'` traces for iteration 2: the feedback section should be 2–5 compact bullet lines, not character garbage or long prose.
- Confirm `prompt_tokens` for `critic_evaluator` on iteration 2+ are lower than iteration 1 in the same session.
- Manually review the final resume quality. If structured bullets cause the generator to miss a broad structural issue, add a `[CONTEXT]` tag to capture it.

> **Implementation note (2026-06-25):**
> - **2.1:** `build_evaluator_prompt()` in `prompts.py` — replaced the `<task_instructions>` block with a 5-tag schema (`[VERB]`, `[METRIC]`, `[HALLUCINATION]`, `[KEYWORD]`, `[CONTEXT]`). Updated the in-context example JSON to use the tagged format so Gemini sees a consistent reference. No schema or loop changes needed.
> - **2.2:** `tailor_process()` in `bot.py` — builds `skills_fingerprint` once before the loop by flattening all skill lists from `master_profile_json`. On each iteration, `evaluator_profile` is set to the full `master_profile_json` on iteration 1 and `skills_fingerprint` on iterations 2+. Only the `evaluate_resume()` call was changed; the generator still receives the full profile every time.

---

## Phase 3 — Data-Driven Decisions

**Depends on Phase 2 deployed. Requires querying Supabase before proceeding.**

### Step 3.1 — Decide: reduce `max_iterations` from 3 → 2

**Before implementing,** run this query on the Supabase `evaluations` table:
```sql
SELECT
    iteration_number,
    COUNT(*) AS count,
    ROUND(AVG(passed::int) * 100, 1) AS pct_passed
FROM evaluations
GROUP BY iteration_number
ORDER BY iteration_number;
```

**Decision gate:**
- If fewer than 15% of sessions reach iteration 3 with `passed = false`: safe to cut to 2.
- If more than 20% of sessions reach iteration 3 with `passed = false`: keep 3 until evaluator feedback quality is confirmed better.

**Implementation (if gate passes):**

**File:** `bot/bot.py`, line ~301:
```python
max_iterations = 2  # was 3; safe after structured feedback is in place
```

Also update the UI callback string on line ~307:
```python
await ui_callback(rf"🤖 *Step 1/4:* Generating draft \(Iteration {iteration}/{max_iterations}\)\.\.\.")
```
This already uses `max_iterations` dynamically, so no other change needed.

**Estimated savings:** ~12,000–15,000 tokens when iteration 3 was the only remaining one; ~0 tokens when the loop already exits at iteration 1 or 2 (common case).

---

### Step 3.2 — Decide: move `master_profile_json` into `system_instruction`

**Before implementing,** check Step 1.4 cache data:
```sql
SELECT feature, ROUND(AVG(cached_tokens::float / NULLIF(prompt_tokens, 0)) * 100, 1) AS cache_hit_pct
FROM llm_requests GROUP BY feature;
```

- If `cache_hit_pct` for `tailor_generator` / `critic_evaluator` is > 30%: implicit caching is working; skip this step.
- If `cache_hit_pct` is < 10%: cache prefix alignment is needed; proceed.

**Implementation:**

**File:** `bot/prompts.py` — both `build_generator_prompt()` and `build_evaluator_prompt()`

Move `master_profile_json` from the user message into the system instruction. In `build_generator_prompt()`:

```python
system_instruction = f"""{persona}

## Master Profile (Source of Truth)
{master_profile_json}

## Task
...
```

And remove the `<master_profile>` XML block from `user_message`.

Do the same in `build_evaluator_prompt()`.

**Why this helps caching:** Gemini's implicit cache is keyed on the prompt prefix. The system instruction is always the leading prefix. By placing the largest static blob (`master_profile_json`, ~2,500 tokens) at the top of the system instruction (before the persona, which varies), consecutive calls within a session are more likely to share a cached prefix.

**Note:** The persona should move to *after* the master profile in the system instruction so the profile is the true prefix that stays constant.

**Estimated savings:** 4,000–5,000 tokens per call *if* implicit cache hits improve. No guaranteed savings without the cache hitting.

---

## Phase 4 — Optional / Future

These steps require more engineering effort and should be evaluated based on actual cost data from the analytics after Phases 1–3.

### Step 4.1 — Deterministic keyword pre-check before evaluator

**File:** `bot/bot.py`, inside the generation loop before the `evaluate_resume()` call.

Extract required keywords from the JD (nouns that appear in `<required>` or appear multiple times in the skills/requirements section), then check them against the generated HTML before invoking the LLM evaluator. If more than K keywords are missing, synthesize the feedback in Python and skip the LLM call entirely.

```python
def _extract_jd_keywords(jd: str) -> list[str]:
    # Naive: extract capitalized multi-word technical terms
    # Better: pass to a cheap LLM call once and cache per JD
    ...

def _check_missing_keywords(html: str, keywords: list[str]) -> list[str]:
    return [kw for kw in keywords if kw.lower() not in html.lower()]

missing = _check_missing_keywords(current_html, jd_keywords)
if len(missing) > 3:
    # Synthesize feedback directly, skip LLM evaluator
    feedback = [f"[KEYWORD] missing required keyword '{kw}'" for kw in missing]
    # continue to next iteration
else:
    evaluation, eval_usage = await client.evaluate_resume(...)
```

**Estimated savings:** ~5,000–7,000 tokens (an entire Flash evaluator call) when an early draft is obviously keyword-deficient.

---

### Step 4.2 — Explicit Gemini Cache API

Only worth pursuing if Step 3.2 cache data shows implicit hit rate < 10% after prefix alignment.

**File:** `bot/gemini_client.py`

Use `client.caches.create()` to upload `master_profile_json` + the invariant parts of the system instruction at session start, then pass the `cache_name` as a `cached_content` parameter to subsequent calls.

This guarantees cache savings (not best-effort) and is the highest-value optimization available — but requires managing cache TTL (minimum 5 minutes, billed separately) and adds code complexity.

---

## Implementation Sequence Summary

```
Phase 0 (NOW)         Fix feedback: str → List[str] bug
        ↓
Phase 1 (same session) JD truncation + HTML collapse + template drop + cache logging
        ↓
        [Run 5–10 sessions, verify quality + check Supabase]
        ↓
Phase 2               Tagged bullet feedback + compact skills fingerprint
        ↓
        [Run 5–10 sessions, query evaluations table + cache hit rates]
        ↓
Phase 3               max_iterations 3→2 (if gate passes) + system_instruction move (if cache is low)
        ↓
Phase 4 (optional)    Keyword pre-check, explicit cache API
```

**Combined token savings after Phase 2:** ~30–45% reduction in total loop input tokens vs. current baseline.
