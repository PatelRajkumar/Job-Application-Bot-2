# Implementation Plan: Prompt Quality Improvements

Companion to: [`token_efficiency_implementation_plan.md`](./token_efficiency_implementation_plan.md)

Derived from a prompt audit against 2026 prompt engineering best practices (Gemini 3.x, structured output API, few-shot calibration). Phases are ordered by impact-to-effort ratio. Each phase is independently deployable.

---

## Agent Notes (Save Investigation Time)

> Written 2026-06-25. Read this before touching any phase.

### Key file map
| Concern | File | Notes |
|---|---|---|
| Prompt builders | `bot/prompts.py` | `build_evaluator_prompt()` line 1, `build_generator_prompt()` line 109 |
| Pydantic output models | `bot/models.py` | `ResumeEvaluation`, `ResumeRevisions`, `CompanySnippet` — add `TailoredResumeOutput` here |
| LLM call wrappers | `bot/gemini_client.py` | `refine_resume()` ~line 467, `evaluate_resume()` ~line 620 |
| Response parsing | `bot/gemini_client.py` | `start_chat_session()` parses `===COMPANY_NAME===` / `===TAILORED_HTML===` text markers |

### Why these changes matter
- The generator (`refine_resume` / `start_chat_session`) is the **only major call without a structured output schema** — it text-parses custom markers, making it brittle.
- The evaluator has **one few-shot example, always a failure case** — the model never sees what "passed" or "borderline" looks like, causing miscalibrated scoring.
- "Think step-by-step" is a mild regression on Gemini 3.x which runs an internal reasoning scratchpad; the instruction pushes tokens into visible output unnecessarily.
- "Truth bending" language is ambiguous enough that the generator and evaluator can disagree on what counts as hallucination, causing unnecessary feedback churn.

### What NOT to touch
- `response_schema` and `response_mime_type` on `evaluate_resume()` and `revise_resume()` — already correct.
- Temperature settings — well-tuned: 0.7 generator, 0.2 evaluator/reviser, 0.3 classify.
- XML tag structure in user messages — already follows best practice.
- Instruction placement (end of user message) — already correct.

---

## Phase A — Structured Output for Generator (Highest Priority)

**Eliminates the most fragile part of the pipeline. Zero quality risk.**

### Step A.1 — Add `TailoredResumeOutput` Pydantic model

**File:** `bot/models.py`

Add after the existing model definitions:

```python
class TailoredResumeOutput(BaseModel):
    company_name: str = Field(description="Company name with no spaces, e.g. 'GoogleInc'")
    tailored_html: str = Field(description="Complete tailored resume as a full HTML document")
```

---

### Step A.2 — Remove text-marker output format from generator system instruction

**File:** `bot/prompts.py`, inside `build_generator_prompt()`

Remove this block from `system_instruction` (currently at the bottom, after the Rules section):

```python
## Output Format
Respond with EXACTLY this structure — no extra prose before or after:

===COMPANY_NAME===
<CompanyName with no spaces>

===TAILORED_HTML===
<full tailored HTML content>
```

Replace with:

```python
## Output Format
Return a JSON object with two fields:
- `company_name`: the company name with no spaces (e.g. "GoogleInc")
- `tailored_html`: the complete tailored resume as a full HTML document
```

---

### Step A.3 — Add `response_schema` to both generator call sites

**File:** `bot/gemini_client.py`

**Site 1: `start_chat_session()`** (initial generation, ~line 174)

```python
from models import ResumeEvaluation, ResumeRevisions, CompanySnippet, TailoredResumeOutput

config = types.GenerateContentConfig(
    system_instruction=system_prompt,
    temperature=0.7,
    max_output_tokens=8192,           # was 65536 — HTML resume is ~3k–5k tokens
    response_mime_type="application/json",
    response_schema=TailoredResumeOutput,
)
```

Then replace the text-parsing block that extracts `===COMPANY_NAME===` / `===TAILORED_HTML===` with:

```python
result = TailoredResumeOutput.model_validate_json(response.text)
company_name = result.company_name
tailored_html = result.tailored_html
```

**Site 2: `refine_resume()`** (revision mode, ~line 478)

Same config change and same response parsing replacement. Confirm `refine_resume()` is the call used for iterations 2+ in `tailor_process()` — it should mirror Site 1 exactly.

---

### Step A.4 — Remove cover letter from system instruction if unused

**File:** `bot/gemini_client.py`, inside `start_chat_session()` (~line 171)

```python
if generate_cover_letter:
    system_prompt += "\n\n===COVER_LETTER===\n<full cover letter following specifications>"
```

With structured output, this appended text block is incompatible with `TailoredResumeOutput`. If `generate_cover_letter` is still needed, it should be a separate field on the model or a separate call — not a text append. For now, confirm whether this flag is actively used; if not, remove it. If it is used, add `cover_letter: str | None = None` to `TailoredResumeOutput`.

### Phase A Validation
- Run a full `/tailor` session and confirm no `===COMPANY_NAME===` parsing in logs.
- Check `agent_traces`: `raw_response` for `tailor_generator` should now be valid JSON, not a text block with markers.
- Confirm `company_name` and `tailored_html` are both populated correctly.
- Trigger an edge case (unusual company name with spaces) and verify `company_name` is still clean.

---

## Phase B — Evaluator Few-Shot Calibration

**Depends on nothing. Can run in parallel with Phase A.**

### Step B.1 — Add "passed" and "borderline" examples to evaluator

**File:** `bot/prompts.py`, inside `build_evaluator_prompt()`

Currently there is one JSON example in `## Output Instructions` — a failed/hallucinated resume. The model never sees what a passing or borderline resume looks like.

Replace the single example block with three examples:

```python
## Output Instructions
You will return a JSON object that strictly adheres to the provided schema.

Here are examples covering the three meaningful outcome states:

**Example 1 — Passed (strong resume, no changes needed):**
```json
{
  "passed": true,
  "is_hallucinated": false,
  "feedback": [],
  "ats_score": 28,
  "manual_score": 66
}
```

**Example 2 — Borderline (passes but has addressable improvements):**
```json
{
  "passed": true,
  "is_hallucinated": false,
  "feedback": [
    "[VERB] TechCorp bullet 1: replace 'Worked on CI/CD pipeline' → 'Engineered CI/CD pipeline reducing deployment time by 60%'",
    "[KEYWORD] missing JD keyword 'Terraform' — add to skills section if present in master profile"
  ],
  "ats_score": 24,
  "manual_score": 58
}
```

**Example 3 — Failed with hallucination:**
```json
{
  "passed": false,
  "is_hallucinated": true,
  "feedback": [
    "[HALLUCINATION] Skills section: 'AWS SageMaker' is not in master profile — revert",
    "[VERB] TechCorp bullet 2: replace 'Was responsible for cache layer' → 'Engineered Redis cache layer reducing p99 latency by 35%'",
    "[KEYWORD] missing JD keyword 'Kubernetes' — add to skills section or TechCorp bullets"
  ],
  "ats_score": 25,
  "manual_score": 55
}
```

---

### Step B.2 — Remove dead rubric line

**File:** `bot/prompts.py`, inside `build_evaluator_prompt()`, `## The Dual-Stage Scoring Rubric` section

Remove this line (~line 42):

```
- **Formatting & Parsability (10 pts):** (Assume perfect score of 10/10 as the backend uses a standardized, ATS-optimized HTML template).
```

Replace with:

```
- **Formatting & Parsability (10 pts):** Score 10/10 always — the backend uses a standardized ATS-optimized HTML template.
```

This makes the intent explicit ("always 10") without a parenthetical explaining why, saving a few tokens and reducing model confusion.

---

### Step B.3 — Remove "Think step-by-step" CoT instruction

**File:** `bot/prompts.py`, inside `build_evaluator_prompt()`, the `<task_instructions>` block in `user_message` (~line 95)

Remove:

```
Think step-by-step for the Grounding Check.
```

Gemini 3.x models run an internal reasoning scratchpad. Explicit CoT instructions either push reasoning into visible output (wasting tokens) or are ignored. The `response_schema` already enforces structured output — there is no room for visible chain-of-thought. Remove the line entirely.

### Phase B Validation
- Run 3+ sessions and inspect `agent_traces` where `agent_role = 'evaluator'`.
- Confirm `passed: true` now appears in some sessions (previously may have been over-penalizing).
- Confirm `feedback` arrays are tagged (`[VERB]`, `[KEYWORD]`, etc.) — not free prose.
- Spot-check that `is_hallucinated` is not being triggered on legitimate extrapolations.

---

## Phase C — Tighten Truth-Bending Language

**Depends on Phase A complete (so evaluator/generator are in sync before redefining the boundary).**

### Step C.1 — Replace ambiguous "bend the truth" in generator

**File:** `bot/prompts.py`, inside `build_generator_prompt()`, `## Rules` section (~line 143)

Replace Rules 4 and 5:

```python
4. Believable Extrapolation: You are allowed to "bend the truth" to align with the Job Description. You may invent realistic metrics, specific and interesting problem solutions, and additional pointers IF they make sense given the candidate's existing skills and experience in the `<master_profile>`.
5. Strict Boundary: Do NOT completely fabricate new skills that the candidate is not at all familiar with (i.e. skills entirely missing from the master profile). Do not add completely new skills without explicit permission.
```

With:

```python
4. Truthful Extrapolation: You may rephrase existing experience to be more impactful and specify plausible concrete metrics (e.g. "~40% reduction") where the master profile implies improvement but lacks a precise number. You may emphasize skills that are present in the master profile even if not prominently listed.
5. Hard Boundary: Never introduce job titles, companies, technologies, certifications, or domains that are entirely absent from the master profile. If a JD requires a skill the candidate clearly does not have, omit it rather than fabricate it — the evaluator will flag any fabrication.
```

---

### Step C.2 — Mirror language in evaluator Grounding Check

**File:** `bot/prompts.py`, inside `build_evaluator_prompt()`, `## Grounding Check - CoT (CRITICAL)` section (~line 57)

Replace:

```python
You must allow for realistic "truth bending" to align with the Job Description. It is VALID to invent believable metrics, specific and interesting problem solutions, and additional pointers if they can be logically inferred from or align with the existing skills and experience in the `<master_profile>`.
However, complete fabrications of skills the candidate is NOT at all familiar with (i.e. skills entirely missing from the master profile) without explicit permission are NOT allowed.
```

With:

```python
Rephrased experience and plausible concrete metrics (e.g. "~40% reduction") are VALID if they align with skills and experience already in the master profile.
Introducing job titles, companies, technologies, certifications, or domains entirely absent from the master profile is a HALLUCINATION — flag it with [HALLUCINATION].
When in doubt, check the master profile's `skills` object before flagging. A skill present in the profile but not prominently listed is NOT a hallucination.
```

This ensures the generator and evaluator operate on identical definitions of what is and isn't allowed, eliminating churn where the generator adds something reasonable and the evaluator flags it incorrectly.

### Phase C Validation
- Run 3+ sessions comparing hallucination flag rates before/after.
- Inspect `agent_traces` for `is_hallucinated: true` — verify they are genuine fabrications, not legitimate extrapolations.
- If hallucination flags drop to near-zero (previously being triggered on valid content), Phase C is working.

---

## Phase D — Right-size `max_output_tokens`

**Zero-dependency. Implement any time.**

**File:** `bot/gemini_client.py`

| Call | Current | Recommended | Reason |
|---|---|---|---|
| `evaluate_resume()` | 65536 | 2048 | Evaluator output is <200 tokens (JSON with 3–5 feedback items) |
| `generate_cover_letter()` | 65536 | 4096 | One-page cover letter is ~600–800 tokens |
| `refine_resume()` | 65536 | 8192 | HTML resume is ~3k–5k tokens; headroom for long templates |
| `start_chat_session()` | 65536 | 8192 | Same as above |
| `revise_resume()` | 65536 | 4096 | Structured revision JSON is compact |

Change each `GenerateContentConfig` accordingly. This prevents runaway token generation and signals to the model to be concise — large `max_output_tokens` values can implicitly encourage padding.

### Phase D Validation
- Confirm `completion_tokens` in `llm_requests` table stays well below the new ceilings.
- Confirm no truncation errors (would appear as malformed JSON in `raw_response` in `agent_traces`).

---

## Phase E — Align Evaluator/Generator Personas

**Depends on Phases A–C complete. Lower priority — do after quality baseline is confirmed.**

### Step E.1 — Harmonize `product_startup` persona language

The evaluator currently uses `"AGGRESSIVELY penalize"` / `"Do NOT allow weak verbs"` while the generator uses `"Use high-ownership verbs. Avoid weak verbs."` — softer phrasing for identical standards. When the two agents use different language for the same rule, the generator produces content at a slightly lower bar than the evaluator enforces, causing unnecessary revision loops.

**File:** `bot/prompts.py`

In `build_generator_prompt()`, update the `product_startup` persona to match the evaluator's intensity:

```python
persona = """Act as an expert resume writer for a fast-paced Silicon Valley startup.
You MUST use high-ownership verbs: Architected, Engineered, Scaled, Optimized, Designed, Led.
You MUST NOT use passive verbs: Maintained, Assisted, Worked on, Helped, Was responsible for.
Quantify every achievement using the XYZ format (Accomplished [X] as measured by [Y], by doing [Z]).
The hiring manager will immediately reject any bullet that reads like a duty list rather than an outcome."""
```

Similarly ensure the `gcc` and default `it_services` personas describe the same bar in generator and evaluator.

### Phase E Validation
- Compare iteration counts before/after across 5+ sessions: if personas are aligned, the generator should pass the evaluator on iteration 1 more often.
- Track `pct_passed` at iteration 1 in the `evaluations` table — expect an increase.

---

## Implementation Sequence Summary

```
Phase A (NOW)         response_schema for generator — eliminate text-marker parsing
Phase B (NOW)         Evaluator few-shot calibration + remove dead CoT instruction
        ↓
        [Run 3–5 sessions, check agent_traces for scoring calibration]
        ↓
Phase C               Tighten truth-bending language — align generator/evaluator boundary
Phase D (any time)    Right-size max_output_tokens
        ↓
        [Run 3–5 sessions, compare hallucination flag rates and iteration 1 pass rates]
        ↓
Phase E               Persona alignment — reduce unnecessary revision loops
```

**Combined quality improvement after Phase C:** Expect reduction in `is_hallucinated: true` false positives, lower average iterations per session, and more consistent `ats_score` / `manual_score` values across sessions.
