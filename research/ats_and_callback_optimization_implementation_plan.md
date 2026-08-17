# Implementation Plan: ATS Scoring & Callback Optimization

**Source research:** [ats_and_callback_optimization_research.md](./ats_and_callback_optimization_research.md)
**Date:** 2026-06-26
**Goal:** Translate every finding in the research doc into concrete code changes. The research concludes our low-callback problem is **a relevance/ranking/measurement problem, not a parsing problem** — we already win on parsing (single-column text-layer PDF). So this plan invests where the leverage actually is: JD keyword coverage, exact title surfacing, a default keyword-dense summary, honest metrics, anti-stuffing, content-based one-page enforcement, and outcome tracking.

---

## ⚠️ Cross-cutting invariant: keep Generator and Evaluator in SYNC

**Every behavioural rule added to the Generator MUST have a matching check in the Evaluator, and vice versa.** They are a closed loop — the Generator writes, the Evaluator grades and feeds back, the Generator revises. If one side enforces a rule the other does not, the loop oscillates (Generator adds X, Evaluator doesn't reward it / Evaluator demands Y, Generator was never told to produce it) and wastes iterations + tokens.

Both prompts live in [bot/prompts.py](../bot/prompts.py): `build_generator_prompt()` and `build_evaluator_prompt()`. For each phase below, the **Sync contract** sub-section names the paired rules. When you edit one prompt, edit the other in the same commit and re-verify the pairing.

The single source of truth for what to match against is the **shared JD-keyword artifact** introduced in Phase 1 — both agents consume the *same* extracted keyword/title list so they cannot disagree about what the JD requires.

---

## The hard rule on job titles (read before Phase 2)

From the research's title-fraud analysis. Encode this precisely in **both** prompts:

- **ALLOWED (positioning, honest):** echo the JD's target title verbatim in the **summary** and the new **headline** zone; mirror exact skill/tech keywords.
- **FORBIDDEN (résumé fraud):** modify the `role` field of any `experience[]` entry away from the master profile's truth. `experience[].role` is verifiable via background checks — changing "Software Engineer" → "Senior Software Engineer" is an instant-disqualification event.

This is a **carve-out to the existing hallucination guardrail**, not a loosening of it: the summary/headline MAY state the target role; the work history MAY NOT be inflated. The Evaluator must flag any `experience[].role` that differs from the master profile as a `[HALLUCINATION]`, while explicitly permitting the target title in `summary`/`headline`.

---

## Agent Notes (Save Investigation Time — read before touching any file)

> Initialize on first implementation. Each phase has its own notes block — fill it in after completing the phase so the next agent doesn't re-investigate.

### Status

| Phase | Tier | Status | Notes |
|---|---|---|---|
| 1 — Shared JD keyword extraction + coverage scoring | A | ✅ Done | `JDKeywords` model + `CompanySnippet` extended; `classify_company()` extracts title+keywords; `jd_keywords_block` threaded to both agents; `keyword_coverage`+`missing_keywords` on `ResumeEvaluation`; missing keywords fed back into generator loop. |
| 1.5 — Remove `culture_signals` (token waste) | A | ✅ Done | Stripped from `CompanySnippet`, `classify_company()`, `refine_resume()`/`evaluate_resume()` signatures, `build_generator_prompt()`/`build_evaluator_prompt()` signatures, all `<company_culture>` user-message blocks, and `tailor_process()`/`_clear_session()`/`finalize_resume()` in bot.py. `max_output_tokens` lowered 2048→1024 in `classify_company()`. |
| 2 — Job-title mirroring + headline zone | A | ✅ Done | `headline` field on `TailoredResumeContent`; `{{ HEADLINE_PLACEHOLDER }}` in template; `.headline` CSS; generator Rules 8+9; evaluator Role Relevance + Grounding Check updated; examples updated. |
| 3 — Summary default-on, keyword-dense | A | ✅ Done | Generator Rule 10 + schema note; evaluator Clarity-of-Intent rewritten with −5/−10 deductions + `[CONTEXT]` feedback. |
| 4 — Evaluator parse-hygiene (kill blind 10/10) | B | ✅ Done | Evaluator "Formatting & Parsability" rewritten: 3 real checks (date consistency 4 pts, ASCII punctuation 4 pts, skill-string length 2 pts). Generator Rules 11–13 added (Mon YYYY dates, ASCII-only, concise skill items). `html_builder.py` `_normalize_ascii()` normalizes typographic chars on all plain-text fields; skill-item length warning logged at >50 chars. |
| 5 — Honest metrics + anti-stuffing personas | B | ✅ Done | Generator Rule 3 rewritten (prefer real numbers; conservative qualifiers allowed; no fabricated round numbers); all 3 generator personas softened (natural language, avoid buzzword density); all 3 evaluator personas softened; anti-stuffing `[CONTEXT]` check added to Outcome-Focused Bullets rubric; Quantification rubric updated to flag fabricated precise metrics as `[METRIC]`. No `[[...]]` marker syntax (rejected in favour of simpler conservative-qualifier guidance). |
| 6 — One-page by content, not font-shrink | B | ☐ Not started | Touches `generate_pdf.js` + generator prompt. |
| 7 — Callback outcome tracking | C | ☐ Not started | Schema + analytics_logger + bot commands. |
| 8 — Referral / cold-outreach as first-class lever | C | ☐ Not started | Bot already has email-finder; surface it. |
| 9 — End-to-end validation | — | ☐ Not started | Run after all phases. |

### Key file map (verified 2026-06-26)

| File | Role |
|---|---|
| [bot/prompts.py](../bot/prompts.py) | `build_generator_prompt()` + `build_evaluator_prompt()` — the two synced prompts. |
| [bot/models.py](../bot/models.py) | `TailoredResumeContent`, `ResumeEvaluation`, `CompanySnippet`. Add new fields here. |
| [bot/gemini_client.py](../bot/gemini_client.py) | `classify_company()` (runs first, reads JD w/ grounding), `refine_resume()` (generator), `evaluate_resume()` (critic). |
| [bot/bot.py](../bot/bot.py) | `tailor_process()` ~L222–403 — the orchestration loop. Threads `current_content`, `culture_signals`, `company_type`. |
| [bot/html_builder.py](../bot/html_builder.py) | `build_resume_html()` + `build_education_html()` — pure Python assembly. Add headline rendering here. |
| [resume_template.html](../resume_template.html) | Header at L192–202 (name + contacts, **no headline slot yet**); `{{ SUMMARY_PLACEHOLDER }}` L205; `{{ SKILLS_PLACEHOLDER }}` L216. |
| [generate_pdf.js](../generate_pdf.js) | One-page auto-shrink loop L34–127. Shrinks `--font-size-*` down to `0.8×` then hard-clips to `pageRanges: '1'`. This is the "cramming" mechanism to fix in Phase 6. |
| [bot/analytics_logger.py](../bot/analytics_logger.py) | `log_session_start`, `update_session`, `log_evaluation`, `log_funnel_event`, `log_llm_request`. Supabase-backed. Extend for Phase 7. |
| master_profile.json | Keys: `basics`, `skills`, `experience` (each has `company`, `role`, `startDate`, `endDate`, `location`, `general_responsibilities[]`), `side_projects`, `education`. |

### Architecture facts that constrain this plan
- Generator returns **structured `TailoredResumeContent` JSON** (not HTML); evaluator consumes the **same JSON**; Python assembles HTML once via `build_resume_html()`. (See [structured_generation_implementation_plan.md](./structured_generation_implementation_plan.md) — already shipped.) Do NOT reintroduce HTML into the model loop.
- `classify_company()` already makes ONE Flash + Google-Search call over the JD before the loop. **Fold keyword extraction into it (Phase 1) — do not add a separate LLM round-trip.**
- The loop runs `max_iterations = 2`; the evaluator is skipped on the last iteration (feedback wouldn't be acted on). Iteration 1 evaluator gets the full master profile; iteration 2+ gets a compact `skills_fingerprint`.
- Output is a Chrome-rendered **text-layer PDF**, so CSS `::before` bullets / flexbox headers / `div` titles all extract fine — the earlier template alarms were withdrawn (research §5). **Do not "fix" the single-column template.**

---

# TIER A — Highest callback leverage, low effort

## Phase 1 — Shared JD keyword extraction + coverage scoring

**Research basis:** §1.4 (summary/skills are weight-heavy zones), §1.5 (score bands: 90–100 highly qualified … <50 screened out), §2.3 (recruiter Boolean search on title+skills is THE gating step), §4.1 (Jobscan's match-rate + gap-list is the most copyable feature), §6.2.1 (biggest miss: no keyword extraction/coverage/target), §7 Tier A.1 + A.4.

**Design:** One extraction, two consumers — the sync foundation. Extend the existing `classify_company()` call to also return the JD's `target_title` and a ranked `must_have_keywords[]` / `nice_to_have_keywords[]`. Thread this single artifact to BOTH the generator (to anchor `skills[]` + `summary`) and the evaluator (to score coverage against a 75–80% target and emit a gap list). Because both agents score against the *same* list, they cannot disagree.

**Coverage computation:** the Evaluator LLM judges presence (allowing semantic matches per §1.3 — "Python development" ≈ "Python programming"), but against the *fixed* shared list so the denominator is deterministic. Emit `keyword_coverage` (int %) and `missing_keywords[]`.

### Steps

- [ ] **1.1 — Add `JDKeywords` model.** In [bot/models.py](../bot/models.py), add:
  ```python
  class JDKeywords(BaseModel):
      target_title: str = Field(description="The exact role title from the JD, verbatim (e.g. 'Senior Backend Engineer'). Empty string if none stated.")
      must_have_keywords: list[str] = Field(description="5–12 must-have hard skills, tools, and technologies named or strongly implied as required in the JD. Exact surface forms.")
      nice_to_have_keywords: list[str] = Field(description="0–8 secondary/preferred keywords.")
  ```
- [ ] **1.2 — Extend extraction into `classify_company()`.** In [bot/gemini_client.py](../bot/gemini_client.py), update the system prompt to also extract title + keywords, and change `response_schema` to a combined model (either nest `JDKeywords` inside `CompanySnippet`, or return both — prefer extending `CompanySnippet` with the three fields so it stays one call/one parse). Return them in the result dict alongside `company_type` / `culture_signals`. **No new LLM call.**
- [ ] **1.3 — Thread the artifact through `tailor_process()`.** In [bot/bot.py](../bot/bot.py), pull `target_title`, `must_have_keywords`, `nice_to_have_keywords` from `classify_result` and pass into both `refine_resume()` and `evaluate_resume()` (new kwargs). Format the keyword list as a compact string once (e.g. `jd_keywords_block`).
- [ ] **1.4 — Generator anchors to keywords.** In `build_generator_prompt()`, add a `<jd_keywords>` block to the user message and a Rule: *"Anchor `skills[]` and `summary` to the must-have keywords below where they are TRUE for this candidate (present in the master profile). Use the exact surface forms. Do not invent skills the candidate lacks."*
- [ ] **1.5 — Evaluator scores coverage.** Add `keyword_coverage: int` (0–100) and `missing_keywords: list[str]` to `ResumeEvaluation` in [bot/models.py](../bot/models.py). In `build_evaluator_prompt()`, pass the same `<jd_keywords>` block and instruct: compute % of must-have keywords present (semantic match allowed), **target 75–80%**, list every missing one in `missing_keywords`, and emit a `[KEYWORD]` feedback item per gap. Map coverage to the §1.5 bands in the rubric narrative (≥90 highly qualified, 70–89 qualified, 50–69 borderline, <50 screened out).
- [ ] **1.6 — Wire coverage into the pass/fail + feedback loop.** In `tailor_process()`, fold `missing_keywords` into the `feedback_str` already built from `evaluation.feedback`, and log `keyword_coverage` via `log_evaluation` (extend that logger signature — see Phase 7 if you also add the column; otherwise log into the existing trace).

### Sync contract (Phase 1)
| Generator rule | Evaluator check |
|---|---|
| Anchor `skills[]`+`summary` to `must_have_keywords` (only if true) | `[KEYWORD]` per missing must-have; `keyword_coverage` %, target 75–80 |
| Use exact JD surface forms | Coverage uses the same shared list as denominator |

### Agent Notes — Phase 1
`JDKeywords` added as a standalone model in `models.py` for documentation; the 3 fields (`target_title`, `must_have_keywords`, `nice_to_have_keywords`) were also added directly to `CompanySnippet` so it stays one call/one schema parse. `classify_company()` returns `target_title`, `must_have_keywords`, `nice_to_have_keywords` in its result dict. `bot.py` formats these into `jd_keywords_block` (plain text) and passes it as kwarg `jd_keywords=` to both `refine_resume()` and `evaluate_resume()`, which forward it to their respective prompt builders. `ResumeEvaluation` has new fields `keyword_coverage: int` (default 0) and `missing_keywords: list[str]` (default []). `log_evaluation` signature was NOT extended (deferred to Phase 7); `keyword_coverage` is logged into the evaluator's `agent_trace` parsed_output instead. Missing keywords are appended to the generator's feedback string on each non-passing iteration.

---

## Phase 1.5 — Remove `culture_signals` (token waste, superseded by JD keywords)

**Rationale:** `culture_signals` are 2–4 free-form bullet points about a company's "hidden cultural priorities," generated by `classify_company()` via a grounded Flash call and then stuffed into a `<company_culture>` block on every generator and evaluator call. With Phase 1 done, this is redundant and wasteful:
- The `company_type` persona already handles tone/style differentiation.
- `jd_keywords_block` now carries the actual, concrete, scoreable signal from the JD itself. The JD *is* the culture signal.
- `culture_signals` are generic company-level research that can't be scored, may be inaccurate, and don't change bullet content in a verifiable way.
- They forced `max_output_tokens=2048` in `classify_company()` (bumped from 1024 because free-form text was tight), and recur as prompt tokens on every generator + evaluator call across both iterations.

### Steps

- [ ] **1.5.1 — Strip from `CompanySnippet` model.** In [bot/models.py](../bot/models.py), remove the `culture_signals` field from `CompanySnippet`.
- [ ] **1.5.2 — Strip from `classify_company()`.** In [bot/gemini_client.py](../bot/gemini_client.py): remove item 2 ("2–4 hidden cultural or technical priorities") from the system prompt; change `"Use web search to ground your answer for items 1–2"` to `"Use web search to ground your answer for item 1 (company type) only"` — items 2–4 come from reading the JD, no search needed. Remove `culture_signals` from the returned result dict and from all local variable assignments. Drop `max_output_tokens` back to 1024 (verify it fits; the remaining output is now just `company_type` + the three JD keyword fields).
- [ ] **1.5.3 — Strip from `tailor_process()`.** In [bot/bot.py](../bot/bot.py), remove `culture_signals` from `classify_result` unpacking, `context.user_data`, and the kwargs passed to `refine_resume()` / `evaluate_resume()`.
- [ ] **1.5.4 — Strip from prompt builders.** In [bot/prompts.py](../bot/prompts.py), remove the `culture_signals` parameter from `build_generator_prompt()` and `build_evaluator_prompt()`, and remove the `<company_culture>` block from all three user-message variants (initial generator, revision generator, evaluator). Also remove the sentence `"Use the <company_research> to identify the hidden cultural traits and specific technical stack priorities for this company. Ensure the resume aligns with these priorities."` from the evaluator system instruction (line ~33) — it references a non-existent tag and instructs the model to reason about culture signals it no longer receives.
- [ ] **1.5.5 — Strip from `refine_resume()` / `evaluate_resume()` signatures.** In [bot/gemini_client.py](../bot/gemini_client.py), remove the `culture_signals` kwarg from both method signatures.

### Agent Notes — Phase 1.5
`culture_signals` fully removed. `max_output_tokens` in `classify_company()` lowered 2048→1024. The `<company_culture>` block is gone from all three user-message variants (initial generator, revision generator, evaluator). The stale `<company_research>` sentence also removed from the evaluator system instruction. `CompanySnippet` schema field removed; `classify_company()` no longer returns `culture_signals` in its result dict. Both agent signatures (`refine_resume`, `evaluate_resume`) and both prompt builders no longer accept a `culture_signals` param.

---

## Phase 2 — Job-title mirroring + headline zone

**Research basis:** §2.3 (title Boolean search is the gating step), §3.5 (top-third matters most), §5 ("no title mirroring" confirmed & *upgraded* in importance), §6.2.2 (highest impact, cheap fix), §7 Tier A.2 — **plus the hard title-fraud rule above.**

**Design:** Add an honest **headline** zone under the name (`Software Engineer → targeting <JD target title>` style, or a summary that naturally carries the phrase). Title goes in headline + summary ONLY. `experience[].role` stays exactly as master profile. Uses `target_title` from Phase 1.

### Steps

- [ ] **2.1 — Add `headline` to content model.** In `TailoredResumeContent`, add:
  ```python
  headline: str | None = Field(default=None, description="Positioning headline under the name stating the TARGET role (e.g. 'Software Engineer — targeting Senior Backend / Full-Stack roles'). Echoes the JD target title. NOT a claim about employer records. Plain text only.")
  ```
- [ ] **2.2 — Template slot.** In [resume_template.html](../resume_template.html), add `{{ HEADLINE_PLACEHOLDER }}` between the `name` div (L194) and `contacts` (L195), with an HTML-comment example mirroring the existing placeholder convention. Add a small CSS class (e.g. `.headline`) near `.section-title` (L80).
- [ ] **2.3 — Render headline.** In [bot/html_builder.py](../bot/html_builder.py) `build_resume_html()`, build `headline_html` (escaped, empty string if `None`) and `.replace('{{ HEADLINE_PLACEHOLDER }}', headline_html)`.
- [ ] **2.4 — Generator rule (the carve-out).** In `build_generator_prompt()`:
  - Rule: *"Set `headline` to position toward the JD's target title (`target_title`). Echo it in `summary` too. This is honest positioning, not a job-title claim."*
  - Rule (hard boundary, strengthened): *"NEVER change any `experience[].role` away from the master profile. The target title may appear ONLY in `headline` and `summary`. Inflating a work-history title is fraud and will be flagged."*
- [ ] **2.5 — Evaluator check (mirror).** In `build_evaluator_prompt()`:
  - Reward: Role Relevance (the existing 10pt item) is satisfied when `headline`/`summary` mirror `target_title`.
  - Flag: any `experience[].role` differing from the master profile → `[HALLUCINATION] experience[i].role: title inflated vs master profile — revert to '<truth>'`. Explicitly state the headline/summary target title is permitted and NOT a hallucination.

### Sync contract (Phase 2)
| Generator rule | Evaluator check |
|---|---|
| `headline`/`summary` echo `target_title` | Role-relevance credited when mirrored |
| `experience[].role` immutable vs master profile | `[HALLUCINATION]` if any role inflated; headline/summary title explicitly allowed |

### Agent Notes — Phase 2
`headline: str | None` added to `TailoredResumeContent` (after `company_name`) in `models.py`. `{{ HEADLINE_PLACEHOLDER }}` inserted in `resume_template.html` between `.name` div and `.contacts` div (inside `.header`); `.headline` CSS class added in `<style>` block (`font-size: var(--font-size-small); font-style: italic; margin-top/bottom: 2pt`). `html_builder.py`: `headline_html` rendered as `<div class="headline">…</div>` (empty string if `None`), substituted via `.replace('{{ HEADLINE_PLACEHOLDER }}', headline_html)` before the summary substitution. Generator prompt: Output Schema updated to include `headline` field and added note that `experience[].role` must be copied verbatim from master profile; Rules 8 (headline echoes `target_title`, also echo in summary) and 9 (FRAUD BOUNDARY — experience[].role immutable, target title only in headline/summary) added. Evaluator prompt: Role Relevance (10 pts) updated to award credit when `headline`/`summary` echo `target_title` and explicitly state this is NOT a hallucination; Grounding Check updated with title carve-out (headline/summary allowed) and title fraud rule (experience[].role change → `[HALLUCINATION]`); all three output examples updated to include `keyword_coverage` + `missing_keywords`; Example 3 updated to show role-inflation `[HALLUCINATION]` feedback.

---

## Phase 3 — Summary default-on, keyword-dense

**Research basis:** §1.4 (summary = densest keyword zone), §3.1 (7-sec scan lands here first), §3.5 (results-led summary = highest-value element, 3× screening pass — vendor-flagged but converges with Tier-1), §5 (confirmed: make default-on), §6.2.3, §7 Tier A.3.

**Design:** `summary` currently `str | None` default `None` and the generator treats it as optional. Make it **default-on**, results-led (lead with a number where one truthfully exists), keyword-dense (anchored to Phase 1 must-haves), and carrying the `target_title`. Optional-off only as a last-resort space saver (and after Phase 6, space is freed by cutting content, not dropping the summary).

### Steps

- [ ] **3.1 — Generator: require summary.** In `build_generator_prompt()`, change the schema note and add a Rule: *"`summary` is REQUIRED (2–3 sentences). Lead with a quantified outcome where a real number exists in the master profile; weave in the `target_title` and 2–3 must-have keywords naturally (no stuffing). Omit only if absolutely no space remains after content cuts."*
- [ ] **3.2 — Evaluator: reward present keyword-dense summary, penalize absence.** Mirror in `build_evaluator_prompt()` under "Clarity of Intent (10pts)": full credit only when summary is present, leads with impact, carries target title + must-have keywords; deduct + emit `[CONTEXT]` feedback if missing or generic.
- [ ] **3.3 — Keep model default sensible.** Leave the Pydantic default `None` (so old paths don't break), but the prompt makes population the norm. `html_builder` already renders empty string when `None`.

### Sync contract (Phase 3)
| Generator rule | Evaluator check |
|---|---|
| Summary required, results-led, keyword-dense, carries target title | Clarity-of-Intent full credit only when present + keyword-dense; flag if absent/generic |

### Agent Notes — Phase 3
Generator: Output Schema `summary` description updated to "REQUIRED in almost all cases — see Rule 10"; Rule 10 added: summary required (2–3 sentences), lead with quantified outcome from master profile, weave in `target_title` + 2–3 must-have keywords naturally, omit only if absolutely no space remains after content cuts. Evaluator: "Clarity of Intent (10 pts)" rewritten — full 10 pts requires summary present + results-led + carries target title + 2–3 keywords; −5 for generic; −10 for absent; `[CONTEXT]` feedback emitted on any deduction. Pydantic model `summary: str | None = None` left unchanged (per step 3.3 — old paths don't break; prompt drives population).

---

# TIER B — Quality / credibility

## Phase 4 — Evaluator parse-hygiene (kill the blind 10/10)

**Research basis:** §2.4 (what actually breaks parsing), §5 (formatting-10/10 confirmed as a rubric weakness — PDF is format-safe but content-level parse risks still exist), §6.2.5, §7 Tier B.5.

**Design:** The "Formatting & Parsability (10 pts): Score 10/10 always" line gives away the check. The container IS safe (single-column text PDF), but **content-level** hygiene can still break downstream parsing/readability. Replace the blind grant with real checks the model can actually evaluate from the structured JSON.

### Steps

- [ ] **4.1 — Rewrite the Formatting rubric item** in `build_evaluator_prompt()`: keep the 10 pts but score against:
  - Date format consistency across all `experience[]`/`projects[]` (e.g. all `Mon YYYY`).
  - No exotic Unicode leaking into text (smart quotes, em/en dashes, decorative bullets, arrows — §2.4).
  - Skill strings not absurdly long (no run-on `items[]` that read as keyword dumps).
  - Contact info present in body (not relied on in header-only — research: never put contact in headers/footers).
  - Deduct per violation; emit `[CONTEXT]` feedback naming the exact field.
- [ ] **4.2 — Generator mirror.** Add generator Rules: consistent date format `Mon YYYY`; ASCII punctuation only (no smart quotes / em dashes / decorative glyphs); keep `items[]` concise.
- [ ] **4.3 — (Optional, defensive) Python lint.** In `html_builder.py` or a small validator, normalize smart quotes/dashes to ASCII and flag over-long skill strings before assembly. Cheap insurance independent of the model.

### Sync contract (Phase 4)
| Generator rule | Evaluator check |
|---|---|
| Consistent `Mon YYYY` dates; ASCII punctuation; concise `items[]` | Formatting pts scored on date consistency / Unicode / skill-length / contact-in-body |

### Agent Notes — Phase 4
Evaluator "Formatting & Parsability" rubric rewritten from a blind grant to three scored checks: date consistency (4 pts — `Mon YYYY` format required, 2 pts deducted per violation), ASCII punctuation (4 pts — smart quotes/em/en dashes flagged, 2 pts per affected field), skill-string length (2 pts — run-on single items flagged). Emit `[CONTEXT]` on any deduction. Generator Rules 11–13 added (Mon YYYY dates; ASCII-only punctuation; one technology per `skills[].items` entry, no comma-packed dumps). Python normalizer (step 4.3) is in `html_builder.py`: `_normalize_ascii()` runs on every `_escape()` call (covers all plain-text fields — company, role, dates, summary, skill names; not bullets since those are trusted for `<strong>/<em>`). Over-long skill items (>50 chars) emit a `logger.warning` before assembly — defensive lint independent of the model loop.

---

## Phase 5 — Honest metrics + anti-stuffing personas

**Research basis:** §1.3 (semantic systems now flag keyword-stuffing & AI filler), §3.2 (76% of recruiters want NATURAL keyword use; <20% include real metrics so genuine ones differentiate), §3.3 (quantification helps but fabricated round numbers are a credibility risk in interviews), §6.2.6, §6.2.7, §7 Tier B.6.

**Design:** Two coupled credibility fixes, both touching personas in BOTH prompts:
1. **Constrain metric fabrication** — prefer real numbers from the master profile; if extrapolating, keep conservative/defensible and **mark** extrapolated metrics so the user can confirm.
2. **Soften aggressive personas** — the startup persona's "AGGRESSIVELY penalize / demand high-ownership verbs" pushes toward buzzword density. Balance ownership verbs with natural language.

### Steps

- [ ] **5.1 — Rewrite the "Truthful Extrapolation" rule** in `build_generator_prompt()` (currently Rule 3): *"Prefer real numbers from the master profile. You MAY add a conservative, defensible estimate only where the profile clearly implies improvement"*
- [ ] **5.2 — Generator personas: dial back stuffing.** In all three personas (startup/gcc/it_services), replace "AGGRESSIVELY penalize" / "demand" framing with "prefer high-ownership verbs **in natural language**; avoid both passive duty-lists AND buzzword density — recruiters reward natural keyword use (§3.2)."
- [ ] **5.3 — Evaluator mirror.** In `build_evaluator_prompt()`:
  - Add an anti-stuffing check: penalize keyword density / repeated terms / AI-filler phrasing; emit `[CONTEXT]` when language reads stuffed.
  - Soften the three evaluator personas in parallel with 5.2 so grader and writer agree on tone.


### Sync contract (Phase 5)
| Generator rule | Evaluator check |
|---|---|
| Prefer real numbers; mark estimates `[[...]]`; no fake precise numbers | `[[...]]` allowed-but-flagged; unmarked fabricated precise metric → `[METRIC]` |
| Natural keyword use; no buzzword density | Anti-stuffing `[CONTEXT]` penalty; personas softened on both sides |

### Agent Notes — Phase 5
No `[[...]]` marker syntax — user rejected it in favour of simpler in-prompt guidance. Generator Rule 3 now says: prefer real profile numbers; conservative qualifiers (e.g. "~40% reduction") are acceptable; never invent precise round numbers — evaluator flags them as `[METRIC]`. All 3 generator personas: removed "MUST use" / "MUST NOT" / "will immediately reject" framing; replaced with "Prefer ... in natural language; avoid buzzword density" (consistent across startup/gcc/it_services). All 3 evaluator personas: removed "AGGRESSIVELY penalize" / "demand" / "You must" framing; softened to preferences. Evaluator rubric: Outcome-Focused Bullets now penalizes buzzword density / AI-filler phrasing with `[CONTEXT]` tag; Quantification now distinguishes acceptable conservative estimates from fabricated round numbers (`[METRIC]`). No html_builder.py changes needed (no markers to strip).

---

## Phase 6 — One-page enforcement by content, not font-shrink

**Research basis:** §3.1 (Ladders: "don't cram"), §3.4 (ResumeGo: two pages preferred for 10+ yrs, even entry-level 1.4×; but tech/FAANG norm favors one page for SWE — our constraint is defensible for SWE but not universal), §6.2.4, §7 Tier B.7.

**Current mechanism (the problem):** [generate_pdf.js](../generate_pdf.js) L34–127 shrinks `--font-size-*` down to `0.8×` (≈8pt body at 10pt base — below comfortable print) then hard-clips with `pageRanges: '1'`. Shrinking type to cram contradicts "don't cram."

**Design:** Enforce one page primarily by **cutting/condensing content** (which the generator controls), and only mildly adjust type. Relax the shrink floor and let the generator be the lever. Optionally allow a clean two-page for senior/10+ yr JDs.

### Steps

- [ ] **6.1 — Generator owns length.** In `build_generator_prompt()`, strengthen the existing one-page rule: *"The resume MUST fit one page by SELECTING the top 3–4 bullets per role and 1–2 projects — not by relying on font shrink. Cut the least JD-relevant content first."*
- [ ] **6.2 — Raise the font-shrink floor** in `generate_pdf.js`: change the `if (multiplier < 0.8)` clamp to a higher floor (e.g. `0.92`) so body text never drops below ~9.2pt; if content still overflows at the floor, it's a signal the generator over-produced (log it / surface to retry) rather than cramming.
- [ ] **6.3 — (Optional) Two-page allowance for senior JDs.** Gate on `target_title` / seniority signal from Phase 1: if senior (title contains "Senior/Staff/Lead/Principal" or JD requires 8+ yrs), permit `pageRanges: '1-2'` and tell the generator it may use a second page. Keep one-page default for non-senior SWE.
- [ ] **6.4 — Evaluator awareness.** Minor: evaluator should not penalize a clean two-page senior resume; note in rubric that length follows seniority (§3.4).

### Sync contract (Phase 6)
| Generator rule | Evaluator check |
|---|---|
| Fit length by content selection, not font; two pages only if senior | Don't penalize clean two-page for senior JDs |

### Agent Notes — Phase 6
> _Fill after implementing._ Record the new shrink floor value, whether 6.3 (two-page) was implemented or deferred, and how seniority is detected. Note the `generate_pdf.js` line numbers changed.

---

# TIER C — Measurement & channel (the levers a resume tweak can't fix)

## Phase 7 — Callback outcome tracking

**Research basis:** §3.7 (callbacks driven by timing/volume/referrals/fit a resume can't fix), §4.2 (Teal/Jobscan treat per-application tracking as core), §6.2.8 ("can't improve what we don't measure"; extend existing `analytics_logger`), §7 Tier C.8.

**Design:** Tie resume version → job → outcome so match-rate/persona/keyword-coverage can be correlated with real callbacks. We already log sessions; add an outcome dimension.

### Steps

- [ ] **7.1 — Schema.** Add an `outcome` column/table keyed by `session_id` (Supabase): `status` (applied/callback/rejected/ghosted/interview/offer), `outcome_date`, optional `notes`. Reuse the existing session row if simplest.
- [ ] **7.2 — Logger API.** In [bot/analytics_logger.py](../bot/analytics_logger.py), add `log_outcome(session_id, status, notes=None)` and extend `log_evaluation` to also persist `keyword_coverage` from Phase 1.
- [ ] **7.3 — Bot command.** Add a `/outcome` flow (or reply-to-session) in [bot/bot.py](../bot/bot.py) so the user can mark what happened to each tailored resume. Surface a periodic summary (callback rate by company_type / persona / coverage band).
- [ ] **7.4 — Close the loop.** Document the query that correlates `keyword_coverage` band (§1.5) and `company_type` with callback rate, so persona/keyword strategy can be tuned from data.

### Agent Notes — Phase 7
> _Fill after implementing._ Record the final schema (table vs column), the `/outcome` UX, and the correlation query.

---

## Phase 8 — Referral / cold-outreach as a first-class lever

**Research basis:** §2.3 (referrals bypass the search-and-rank bottleneck — the real wall), §3.7 (single highest-leverage channel), §6.2.9 (bot already has an email-finder / cold-outreach flow — "lean into it"), §7 Tier C.9.

**Design:** The resume is necessary-but-not-sufficient. Make the existing outreach flow ([bot/email_drafter.py](../bot/email_drafter.py)) a prominent, default next step after a resume is delivered — not an afterthought.

### Steps

- [ ] **8.1 — Surface outreach post-tailor.** After `finalize_resume()` delivers the PDF, prompt the user with a clear CTA to draft a referral/cold-outreach message for this company (wire to the existing email-drafter flow).
- [ ] **8.2 — Honest framing message.** Add a one-time/contextual note telling the user plainly: callback volume is largely a channel/volume/timing problem (§3.7) — referrals and application volume matter as much as the document. Keep it short, show once.
- [ ] **8.3 — (Optional) Tie outreach into Phase 7 tracking** so referral-channel applications can be compared against cold applications for callback rate.

### Agent Notes — Phase 8
> _Fill after implementing._ Record how the CTA is surfaced and whether outreach is tagged in outcome tracking.

---

## Phase 9 — End-to-end validation

Run after all phases. Confirm the synced loop behaves.

- [ ] **9.1 — Sync audit.** Diff `build_generator_prompt()` vs `build_evaluator_prompt()`: every Generator rule from Phases 1–6 has a paired Evaluator check and vice versa. Walk the Sync-contract tables.
- [ ] **9.2 — Keyword coverage works.** Run a real `/tailor`; confirm `keyword_coverage` is computed, `missing_keywords` feeds back, and a 2nd iteration raises coverage toward 75–80%.
- [ ] **9.3 — Title integrity.** Confirm `experience[].role` matches master profile exactly while `headline`/`summary` carry the JD target title. Try a JD with a higher title and verify no inflation in work history.
- [ ] **9.4 — Summary present & dense; metrics honest.** Confirm summary is default-on and keyword-dense; confirm extrapolated metrics are `[[marked]]` and surfaced to the user, brackets stripped in the rendered PDF.
- [ ] **9.5 — Parse hygiene.** Confirm evaluator no longer blind-grants 10/10; feed it a draft with mixed date formats / smart quotes and verify it flags them.
- [ ] **9.6 — One-page by content.** Confirm font no longer shrinks below the new floor; over-long drafts get cut, not crammed. Run the copy-paste test (§2.4) on the output PDF — text extracts cleanly and in order.
- [ ] **9.7 — Tracking + outreach.** Mark an outcome via `/outcome`; confirm it persists. Confirm the post-tailor outreach CTA appears.
- [ ] **9.8 — Token sanity.** Confirm folding extraction into `classify_company()` added no extra LLM round-trip and the loop still runs ≤2 iterations.

### Agent Notes — Phase 9
> _Fill after validating._ Record what passed/failed and any follow-ups.

---

## Research → Phase traceability (completeness check)

Every actionable item in the research maps to a phase:

| Research item | Phase |
|---|---|
| §1.4 keyword placement weight (summary/skills) | 1, 3 |
| §1.5 score bands | 1 |
| §2.3 recruiter Boolean title+skill search | 1, 2 |
| §2.4 what breaks parsing (dates, Unicode, headers, columns) | 4, 9.6 (copy-paste test) |
| §3.1 7-sec scan / "don't cram" | 3, 6 |
| §3.2 natural keyword use (76%) / real metrics rare | 5 |
| §3.3 quantification vs fabrication risk | 5 |
| §3.4 one vs two page (ResumeGo) | 6 |
| §3.5 summary highest-value element | 3 |
| §3.6 XYZ formula (already done) | kept (5 personas) |
| §3.7 channel/volume/referrals/timing | 7, 8 |
| §4.1 Jobscan match-rate + gap list | 1 |
| §4.2 Teal per-application tracking | 7 |
| §4.4 Resume Worded critique loop (already = our evaluator) | kept, sharpened in 1,4,5 |
| §5 title mirroring upgraded; 10/10 weakness; summary required | 2, 4, 3 |
| §6.2.1 no keyword extraction/coverage | 1 |
| §6.2.2 no title mirroring | 2 |
| §6.2.3 summary optional | 3 |
| §6.2.4 font-shrink cramming | 6 |
| §6.2.5 blind 10/10 formatting | 4 |
| §6.2.6 invented metrics | 5 |
| §6.2.7 keyword-stuffing personas | 5 |
| §6.2.8 no outcome measurement | 7 |
| §6.2.9 referral/outreach underused | 8 |
| Hard title-fraud rule (experience[].role immutable) | 2 |
| Keep generator + evaluator in sync (user directive) | all phases — Sync contracts + 9.1 |

---

## Implementation sequence

```
Phase 1  (foundation — shared keyword artifact; both agents depend on it)
   ↓
Phase 2  (uses target_title from 1)   ┐
Phase 3  (summary carries keywords)   ├─ Tier A, ship together, validate callbacks
   ↓                                  ┘
Phase 4, 5, 6  (Tier B credibility — independent of each other; 6 touches generate_pdf.js)
   ↓
Phase 7, 8     (Tier C measurement/channel — 8 surfaces existing email flow)
   ↓
Phase 9        (end-to-end + sync audit)
```

**Hard ordering constraint:** Phase 2 depends on Phase 1 (`target_title`). Everything else is independent but Tier A first (highest callback leverage). After EVERY prompt edit, re-check the paired prompt — the sync invariant is the whole point.
