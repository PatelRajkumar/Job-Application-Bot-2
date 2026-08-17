# Implementation Plan: Structured Slot Generation

**Problem:** The generator produces a full JSON-encoded HTML document (~7,000–9,000 output tokens), and the evaluator receives the same HTML back as input — even though it immediately strips all tags before reading it. Both are sending and receiving tokens for structure that carries no semantic value.

**Solution:** Gemini returns and receives only structured content (`TailoredResumeContent` JSON) throughout the entire loop. Python assembles the final HTML once, after the loop exits. The template and HTML structure never touch the model.

---

## Token savings projection

| | Current | After |
|---|---|---|
| Generator input (initial gen) | ~8,000–10,000 (master profile + template + JD) | ~5,000–7,000 (no template) |
| Generator output (initial gen) | ~7,000–9,000 (full JSON-encoded HTML) | ~500–800 (structured content only) |
| Generator output (revision loop) | ~7,000–9,000 (full JSON-encoded HTML) | ~500–800 (same schema, no HTML) |
| Evaluator input (per iteration) | ~2,000–3,000 (stripped HTML, still tag-heavy) | ~500–800 (structured JSON — no stripping needed) |
| **Total reduction** | — | **~88–92% output token reduction; ~60–70% evaluator input reduction** |

At Pro pricing ($2/1M input, $12/1M output): saves ~$0.09–0.12 per full tailor session.

---

## Agent Notes (Save Investigation Time)

> Written 2026-06-25. Updated 2026-06-25 after Phase 6 completion. Read before touching any file.

### Status

| Phase | Status | Notes |
|---|---|---|
| Phase 1 — Pydantic models | ✅ Done | `SkillRow`, `ExperienceRole`, `Project`, `TailoredResumeContent` added to `bot/models.py`. `TailoredResumeOutput` kept in place (Phase 5.2 removes it). |
| Phase 2 — HTML assembler | ✅ Done | `bot/html_builder.py` created. Unit test `bot/test_html_builder.py` passes — all 5 placeholders substituted, `<strong>` in bullets preserved. |
| Phase 3 — Generator prompt | ✅ Done | `build_generator_prompt()`: Task + Rules + Output Format replaced with new schema instructions; `<resume_template>` block removed from initial-gen user message; `current_html` → `current_content`; revision instruction updated. `build_evaluator_prompt()`: `current_html` → `current_content_json`; JSON format hint added after `<current_draft>`; all 3 few-shot examples now use JSON path refs (e.g. `experience[0].bullets[0]`, `skills[3].items`). |
| Phase 4 — gemini_client + bot | ✅ Done | See notes below. |
| Phase 5 — Cleanup | ✅ Done | See Phase 5 notes below. |
| Phase 6 — Validation | ✅ Done | See Phase 6 notes below. |

### Template placeholder map

File: `resume_template.html` — exactly 5 slots, in document order:

| Placeholder | Location in template | Content type |
|---|---|---|
| `{{ SUMMARY_PLACEHOLDER }}` | Line 205, between header and skills | Optional full section block |
| `{{ SKILLS_PLACEHOLDER }}` | Line 216, inside `<ul class="skills-list">` | One or more `<li>` strings |
| `{{ EXPERIENCE_PLACEHOLDER }}` | Line 225, inside `<ul class="subheading-list">` | One or more `<li class="subheading-item">` blocks |
| `{{ PROJECTS_PLACEHOLDER }}` | Line 247, inside `<ul class="subheading-list">` | One or more `<li class="subheading-item">` blocks |
| `{{ EDUCATION_PLACEHOLDER }}` | Line 265, inside `<ul class="subheading-list">` | One or more `<li class="subheading-item">` blocks |

### Current flow (what you're replacing)
1. Generator receives full template HTML + JD + master profile → returns full JSON-encoded HTML document
2. Evaluator receives full HTML → immediately strips all tags → evaluates plain text
3. Generator revision receives stripped-then-reassembled HTML → returns full HTML again
4. HTML is assembled and reassembled on every iteration

### What you're replacing it with
```
Generate  →  TailoredResumeContent JSON  (structured slots, no HTML)
               ↓
Evaluate  →  TailoredResumeContent JSON  (no stripping needed — already structured)
               ↓
Revise    →  TailoredResumeContent JSON  (same schema, updated values)
               ↓
Assemble  →  build_resume_html()  called ONCE after loop exits
```
- `build_resume_html()` in `bot/html_builder.py` is the only place HTML is produced (**already implemented**)
- Template and HTML structure never reach the model at any stage
- Evaluator's HTML stripping code (`re.sub` passes) is removed entirely (Phase 5.1)

### Key constraint: bullets can contain `<strong>` and `<em>` tags
The model currently uses `<strong>` inline in bullet text (e.g. `Engineered <strong>scalable systems</strong>`). The new schema must allow this in bullet strings — Python wraps them in `<li>` but the inner content is model-generated and may include inline HTML emphasis tags. This is intentional and safe since the model is constrained to only those two tags via the prompt rules.

### Revision mode boundary
- **Initial generation (iteration 1):** `start_chat_session()` → returns `TailoredResumeContent` JSON
- **Evaluation (every iteration):** `evaluate_resume()` → receives `TailoredResumeContent` JSON, not HTML. Returns feedback referencing JSON field paths (e.g. `experience[0].bullets[1]`).
- **Revision loop (iterations 2+):** `refine_resume()` → receives `TailoredResumeContent` JSON + feedback → returns updated `TailoredResumeContent` JSON
- **HTML assembly:** `build_resume_html()` called once after the loop exits with the final `TailoredResumeContent`
- **User-triggered manual revision:** `revise_resume()` — unchanged. It operates on the assembled HTML with targeted text replacement. Not part of this plan.

### Evaluator feedback location references
With structured input, the evaluator can reference exact JSON field paths instead of prose location descriptions:
- `[VERB] experience[0].bullets[2]: replace "..." → "..."`
- `[HALLUCINATION] skills[3].items: 'AWS SageMaker' not in master profile`
- `[KEYWORD] missing 'Kubernetes' — add to skills or experience[0].bullets`

The generator in revision mode receives the same JSON and these paths are unambiguous.

### Phase 3 starting point — what to look for in prompts.py
Before editing, read the current `build_generator_prompt()` in `bot/prompts.py` carefully. The prompt quality plan (Phases A–D) was already applied before this plan — so the `## Task`, `## Rules`, and `## Output Format` sections may differ from the original shown in Steps 3.1–3.3. **Match by intent, not literally.**

Key things to do in Phase 3:
1. Update `## Task` to say "return structured JSON content, not HTML" (Step 3.1)
2. Update `## Rules` — remove HTML-structure rules, add JSON schema field rules (Step 3.2)
3. Remove `## Output Format` block with `===COMPANY_NAME===` / `===TAILORED_HTML===` markers if still present (Step 3.3)
4. Remove `<resume_template>` block from the initial-gen user message (Step 3.4)
5. Rename `current_html` → `current_content` in the revision mode branch (Step 3.5)
6. Update `build_evaluator_prompt()` — swap `current_html` for `current_content_json` in signature and user message, add JSON path reference instruction, update few-shot examples (Step 3.6)

### Phase 4 implementation notes (for future agents)

All changes applied 2026-06-25. Files touched: `bot/gemini_client.py`, `bot/bot.py`.

**`bot/gemini_client.py`:**
- Import line changed: `TailoredResumeOutput` → `TailoredResumeContent`, added `build_resume_html, build_education_html` from `html_builder`.
- `refine_resume()` signature: `current_html` → `current_content` (JSON str), added `education_html: str = ''`. `response_schema` now `TailoredResumeContent`, `max_output_tokens` 32768 → 4096. Parses via `TailoredResumeContent.model_validate_json()`, calls `build_resume_html(template_html, content_obj, education_html)` to assemble HTML. Returns `current_content_json` (raw JSON str) alongside `tailored_html`.
- `evaluate_resume()` signature: `current_html` → `current_content_json`. Three `re.sub` HTML-stripping lines removed. Prompt builder call updated to pass `current_content_json` directly.
- `import re` kept — still used in `parse_final_response()` and `start_chat_session()` (skill file sanitization).
- `TailoredResumeOutput` import removed from this file; `TailoredResumeOutput` class still exists in `models.py` (removed in Phase 5.2).

**`bot/bot.py` (`tailor_process()`):**
- Old `current_html = None / feedback = None / max_iterations = 2` block removed (was before skills fingerprint build).
- `build_education_html(master_profile_json)` called once before the loop. `from html_builder import build_education_html` inline import added.
- Loop variable init: `current_content = None`, `current_html = None`, `feedback = None`, `max_iterations = 2` — set just before the loop (after education_html).
- `refine_resume()` call: `current_html=` → `current_content=`, `education_html=education_html` added.
- After resp: `current_content = resp['current_content_json']` stored for the next iteration.
- `evaluate_resume()` call: `current_html=current_html` → `current_content_json=current_content`.
- `finalize_resume()` still uses `fake_raw = f"===COMPANY_NAME===\n{company_name}\n\n===TAILORED_HTML===\n{current_html}"` — this compatibility shim is intentional; `parse_final_response()` is removed in Phase 5 cleanup.

**What was NOT changed (intentional):**
- `start_chat_session()` — legacy chat path, not used by `tailor_process()`. Left as-is.
- `revise_resume()` — user-triggered stateless revision still operates on HTML. Not part of this plan.
- `TailoredResumeOutput` model — still in `models.py`, removed in Phase 5.2 after validation.

### Phase 5 implementation notes (for future agents)

All changes applied 2026-06-25. Files touched: `bot/models.py`, `bot/gemini_client.py`, `bot/prompts.py`.

**Step 5.1 (HTML stripping):** Was already removed during Phase 4. `evaluate_resume()` in `gemini_client.py` has no `re.sub` lines. `import re` remains — still needed by `start_chat_session()` (skill file sanitization) and `parse_final_response()`/`is_final_output()` (the `fake_raw` shim used by `revise_resume()` / `handle_iteration` / `handle_reply`).

**Step 5.2 (Remove `TailoredResumeOutput`):**
- Deleted `class TailoredResumeOutput` from `bot/models.py`. Removed the backward-compat comment block too.
- `start_chat_session()` in `gemini_client.py` was the only remaining reference (`response_schema=TailoredResumeOutput`). Updated to `TailoredResumeContent`. `start_chat_session` is the legacy chat path — NOT used by `tailor_process()` in production. Updating the schema there is safe.

**Step 5.3 (Remove `template_html` from `build_generator_prompt()`):**
- Removed `template_html: str` parameter from `build_generator_prompt()` signature in `bot/prompts.py`. The parameter was dead — the function never referenced it internally.
- Updated the single call site in `refine_resume()` (`gemini_client.py`) to drop the `template_html` positional arg.
- `template_html` still exists as a parameter of `refine_resume()` itself (passed on to `build_resume_html()` for HTML assembly) — correct and intentional. `bot.py` still passes `template_html=template_html` to `refine_resume()` — no change needed there.

**Step 5.4 (Update sibling plan files):** No-op — sibling plans had no references to `32768`.

**Validation:** `python -c "from models import TailoredResumeContent; from prompts import build_generator_prompt; import inspect; print(list(inspect.signature(build_generator_prompt).parameters.keys()))"` → `['company_type', 'master_profile_json', 'jd', 'culture_signals', 'current_content', 'feedback']` ✅

### Phase 6 implementation notes (for future agents)

All validation completed 2026-06-25. No source file changes required — all checks passed cleanly.

**6.1 — Unit test:** `python test_html_builder.py` from `bot/` → `All assertions passed.` ✅

**6.2 — Visual diff:** Manual check required (cannot automate). Open a generated HTML file in a browser and compare against a pre-migration reference.

**6.3 — Token counts:** Query Supabase after 3+ sessions to confirm `avg_output` drops from ~7,000–9,000 to ~500–800 for `feature = 'tailor_generator'`.

**6.4 — Evaluator structured input confirmed:**
- `build_evaluator_prompt()` signature: `(company_type, master_profile_json, jd, current_content_json, culture_signals)` ✅
- No `re.sub` HTML-stripping lines in `evaluate_resume()` ✅
- Few-shot examples in prompts use JSON path refs (`experience[0].bullets[1]`, `skills[3].items`) ✅
- User message includes format hint: `The <current_draft> is a JSON object with fields: summary, skills[], experience[], projects[].` ✅

**6.5 — HTML assembly happens once per session:**
- `build_resume_html()` is called inside `refine_resume()` in `gemini_client.py` (line 476) — once per generator call ✅
- `build_resume_html` has **0 references in `bot.py`** — assembly never happens in the loop coordinator ✅
- The loop in `bot.py` stores `current_html = resp['tailored_html']` but only uses it for the `fake_raw` shim at the end (post-loop) ✅
- For a 2-iteration session: `refine_resume()` is called twice (iterations 1 and 2), so `build_resume_html()` is called twice total. The evaluator on iteration 1 receives `current_content` (JSON) not `current_html`. HTML is not re-assembled between evaluator and next generator. This is the intended behavior per Phase 4.5.

**6.6 — `revise_resume()` unaffected:** All 4 `current_html` references in `gemini_client.py` are confined to `revise_resume()` — the manual user-triggered revision path. No changes were made to it. ✅

**Stale log strings fixed (minor):**
- `bot.py` line 380: `prompt_text="Evaluating current_html"` → `"Evaluating current_content_json"`
- `gemini_client.py` line 675: same fix in the `evaluator_failed` error trace path

**Confirmed invariants (re-run any time to sanity-check):**
```
python test_html_builder.py
python -c "from models import TailoredResumeContent; from prompts import build_generator_prompt, build_evaluator_prompt; import inspect; print(list(inspect.signature(build_generator_prompt).parameters.keys())); print(list(inspect.signature(build_evaluator_prompt).parameters.keys()))"
```
Expected output:
```
All assertions passed.
['company_type', 'master_profile_json', 'jd', 'culture_signals', 'current_content', 'feedback']
['company_type', 'master_profile_json', 'jd', 'current_content_json', 'culture_signals']
```


## Phase 1 — New Pydantic Models [Done]

**File:** `bot/models.py`

Add these models after the existing definitions. Keep `TailoredResumeOutput` in place for now — you will remove it in Phase 4 after all call sites are migrated.

```python
class SkillRow(BaseModel):
    category: str = Field(description="Category label, e.g. 'Languages', 'Frameworks & ORM'")
    items: list[str] = Field(description="Individual skill names in this category")

class ExperienceRole(BaseModel):
    company: str = Field(description="Company name as it should appear on the resume")
    start_date: str = Field(description="Start month and year, e.g. 'Jan 2022'")
    end_date: str = Field(description="End month and year, or 'Present'")
    role: str = Field(description="Job title / role")
    location: str = Field(description="City, State or 'Remote'")
    bullets: list[str] = Field(description="3–5 impact bullets. May contain <strong> and <em> tags for emphasis. No other HTML tags.")

class Project(BaseModel):
    name: str = Field(description="Project name")
    tech_stack: list[str] = Field(description="Technologies used, e.g. ['Node.js', 'PostgreSQL', 'Redis']")
    start_date: str = Field(description="Start month and year")
    end_date: str = Field(description="End month and year, or 'Present'")
    bullets: list[str] = Field(description="2–3 impact bullets. May contain <strong> and <em> tags. No other HTML tags.")

class TailoredResumeContent(BaseModel):
    company_name: str = Field(description="Company name with no spaces, used as file name, e.g. 'DocusignInc'")
    summary: str | None = Field(default=None, description="Optional 2–3 sentence professional summary. Plain text only — no HTML tags.")
    skills: list[SkillRow] = Field(description="5–7 skill rows, ordered by relevance to the JD")
    experience: list[ExperienceRole] = Field(description="Work experience entries, most recent first. Only include roles present in the master profile.")
    projects: list[Project] = Field(description="1–2 most relevant projects for this JD. Only include projects present in the master profile.")

# Note: EducationEntry is intentionally omitted from TailoredResumeContent.
# Education never changes between tailored resumes — it is hardcoded in build_resume_html()
# from master_profile_json rather than sent to the model.
```

### Validation
- Run `python -c "from models import TailoredResumeContent; print('OK')"` from the `bot/` directory.
- Confirm no import errors.

---

## Phase 2 — HTML Assembler [Done]

**File:** `bot/html_builder.py` (new file)

This is pure Python — no LLM dependencies, no imports from other bot modules. Takes the structured content and the raw template string, returns the completed HTML string.

Education is hardcoded — never sent to the model. `build_resume_html()` accepts a pre-built `education_html` string and substitutes it directly. This string is computed once from `master_profile_json` at the start of `tailor_process()` using a helper function also defined in this file.

```python
from models import TailoredResumeContent
import html as _html
import json


def _escape(text: str) -> str:
    """HTML-escape plain-text fields (summary, company names, dates, etc.)."""
    return _html.escape(text)


def build_education_html(master_profile_json: str) -> str:
    """
    Builds the {{ EDUCATION_PLACEHOLDER }} HTML from master_profile_json.
    Called once per session — education never changes between tailored resumes.
    Expected master_profile shape: { "education": [{ "institution", "location", "degree" }] }
    """
    profile = json.loads(master_profile_json)
    edu_entries = profile.get("education", [])
    items = []
    for edu in edu_entries:
        items.append(
            f'<li class="subheading-item">\n'
            f'                <div class="subheading-row row-1">\n'
            f'                    <span class="left">{_escape(edu.get("institution", ""))}</span>\n'
            f'                    <span class="right">{_escape(edu.get("location", ""))}</span>\n'
            f'                </div>\n'
            f'                <div class="subheading-row row-2">\n'
            f'                    <span class="role">{_escape(edu.get("degree", ""))}</span>\n'
            f'                    <span class="location"></span>\n'
            f'                </div>\n'
            f'            </li>'
        )
    return '\n            '.join(items)


def build_resume_html(template_html: str, content: TailoredResumeContent, education_html: str) -> str:
    """
    Substitutes TailoredResumeContent slot values into the resume template.
    Returns the completed HTML string.
    """
    # ── Summary ──────────────────────────────────────────────────────────────
    if content.summary:
        summary_html = (
            '<div class="section-title">Professional Summary</div>\n'
            '<div style="font-size: var(--font-size-small); margin-top: 4pt; '
            'margin-bottom: 8pt; text-align: justify;">\n'
            f'    {_escape(content.summary)}\n'
            '</div>'
        )
    else:
        summary_html = ''

    # ── Skills ───────────────────────────────────────────────────────────────
    skills_lines = []
    for row in content.skills:
        items_str = ', '.join(_escape(i) for i in row.items)
        skills_lines.append(f'<li><strong>{_escape(row.category)}:</strong> {items_str}</li>')
    skills_html = '\n            '.join(skills_lines)

    # ── Experience ───────────────────────────────────────────────────────────
    exp_items = []
    for role in content.experience:
        bullets_html = '\n                    '.join(
            f'<li>{b}</li>' for b in role.bullets  # bullets may contain <strong>/<em>
        )
        exp_items.append(
            f'<li class="subheading-item">\n'
            f'                <div class="subheading-row row-1">\n'
            f'                    <span class="left">{_escape(role.company)}</span>\n'
            f'                    <span class="right">{_escape(role.start_date)} &ndash; {_escape(role.end_date)}</span>\n'
            f'                </div>\n'
            f'                <div class="subheading-row row-2">\n'
            f'                    <span class="role">{_escape(role.role)}</span>\n'
            f'                    <span class="location">{_escape(role.location)}</span>\n'
            f'                </div>\n'
            f'                <ul class="item-list">\n'
            f'                    {bullets_html}\n'
            f'                </ul>\n'
            f'            </li>'
        )
    experience_html = '\n            '.join(exp_items)

    # ── Projects ─────────────────────────────────────────────────────────────
    proj_items = []
    for proj in content.projects:
        tech_str = ', '.join(_escape(t) for t in proj.tech_stack)
        bullets_html = '\n                    '.join(
            f'<li>{b}</li>' for b in proj.bullets
        )
        proj_items.append(
            f'<li class="subheading-item">\n'
            f'                <div class="project-heading">\n'
            f'                    <span class="project-title"><strong>{_escape(proj.name)}</strong></span>\n'
            f'                    <span class="project-date">{_escape(proj.start_date)} &ndash; {_escape(proj.end_date)}</span>\n'
            f'                </div>\n'
            f'                <div class="project-tech"><em>{tech_str}</em></div>\n'
            f'                <ul class="item-list">\n'
            f'                    {bullets_html}\n'
            f'                </ul>\n'
            f'            </li>'
        )
    projects_html = '\n            '.join(proj_items)

    # ── Substitute ───────────────────────────────────────────────────────────
    # education_html is passed in pre-built — computed once from master_profile_json
    # via build_education_html(), never regenerated by the model.
    html = template_html
    html = html.replace('{{ SUMMARY_PLACEHOLDER }}', summary_html)
    html = html.replace('{{ SKILLS_PLACEHOLDER }}', skills_html)
    html = html.replace('{{ EXPERIENCE_PLACEHOLDER }}', experience_html)
    html = html.replace('{{ PROJECTS_PLACEHOLDER }}', projects_html)
    html = html.replace('{{ EDUCATION_PLACEHOLDER }}', education_html)

    return html
```

### Important: escaping strategy
- `_escape()` is applied to all plain-text fields: company names, dates, roles, locations, skill names, institution names, degrees, summary text.
- Bullet strings (`bullets: list[str]`) are NOT escaped — they are model-generated and may contain `<strong>` and `<em>` tags intentionally. The prompt rules constrain the model to only these two tags.
- If you later want to sanitize bullet HTML (e.g. strip disallowed tags), add a `bleach.clean(b, tags=['strong', 'em', 'b'], strip=True)` call on each bullet before the `f'<li>{b}</li>'` line.

### Validation
Write a unit test or a quick script:

```python
# test_html_builder.py (run from bot/ directory)
from models import TailoredResumeContent, SkillRow, ExperienceRole, Project, EducationEntry
from html_builder import build_resume_html

content = TailoredResumeContent(
    company_name="TestCo",
    summary="A brief summary.",
    skills=[SkillRow(category="Languages", items=["Python", "TypeScript"])],
    experience=[ExperienceRole(
        company="Wishtree Technologies", start_date="Jan 2022", end_date="Present",
        role="Software Engineer", location="Ahmedabad, Gujarat",
        bullets=["Engineered <strong>scalable systems</strong> using Node.js."]
    )],
    projects=[Project(
        name="My Project", tech_stack=["Node.js", "Redis"],
        start_date="Jan 2024", end_date="Present",
        bullets=["Built a fast API."]
    )]
)

master_profile_json = '{"education": [{"institution": "Ganpat University", "location": "Kherva, Gujarat", "degree": "B.Tech in Computer Engineering"}]}'

with open('../resume_template.html') as f:
    template = f.read()

education_html = build_education_html(master_profile_json)
html = build_resume_html(template, content, education_html)
assert '{{ SUMMARY_PLACEHOLDER }}' not in html
assert '{{ SKILLS_PLACEHOLDER }}' not in html
assert '{{ EXPERIENCE_PLACEHOLDER }}' not in html
assert '{{ PROJECTS_PLACEHOLDER }}' not in html
assert '{{ EDUCATION_PLACEHOLDER }}' not in html
assert 'A brief summary.' in html
assert 'Wishtree Technologies' in html
assert '<strong>scalable systems</strong>' in html
assert 'Ganpat University' in html
print("All assertions passed.")
```

---

## Phase 3 — Update Generator Prompt

**File:** `bot/prompts.py`, function `build_generator_prompt()`

### Step 3.1 — Update the Task section in `system_instruction`

Replace:
```python
## Task
You must generate a tailored HTML resume by injecting relevant data from the `<master_profile>` into the `<resume_template>` based on the `<job_description>`.
Use the `<company_culture>` signals to identify hidden cultural and technical priorities for this company, and select/highlight the projects from the master profile that best match these priorities.
You will return exactly the completed HTML and the company name.
```

With:
```python
## Task
Select and tailor resume content from the `<master_profile>` for the `<job_description>` and `<company_culture>` signals.
Return ONLY structured content values. Do NOT generate HTML structure tags, CSS, or layout — Python assembles the final HTML from your output.

## Output Schema
Return a JSON object with these fields:
- `company_name` (str): company name with no spaces, e.g. "DocusignInc"
- `summary` (str | null): optional 2–3 sentence professional summary. Plain text only — no HTML tags.
- `skills` (list of {category, items[]}): 5–7 rows, ordered by relevance to the JD. Only include skills from the master profile.
- `experience` (list of {company, start_date, end_date, role, location, bullets[]}): most recent first, top 3–4 bullets per role. Bullets may use <strong> and <em> for emphasis. No other HTML.
- `projects` (list of {name, tech_stack[], start_date, end_date, bullets[]}): 1–2 most relevant projects. Bullets may use <strong> and <em>. No other HTML.
_(education is not part of the schema — it is hardcoded from the master profile in Python)_
```

### Step 3.2 — Update the Rules section

Replace:
```python
## Rules
1. Do NOT use Markdown formatting inside HTML. Use only proper HTML tags: <strong>, <em>, <b>.
2. Do NOT alter the CSS, layout, margins, or fonts of the template. Only replace the placeholders (e.g. {{ EXPERIENCE_PLACEHOLDER }}) with properly formatted HTML list items as shown in the template comments.
3. Every bullet must contain at least one of: a specific technology name, a metric, an architectural pattern, or a problem name.
4. Believable Extrapolation: ...
5. Strict Boundary: ...
6. The resume must remain within one page — do not add so many bullets that it overflows. Pick the top 3-4 most relevant bullets per role.
7. Only output the final exact text blocks exactly as requested below.
```

With:
```python
## Rules
1. In `bullets` strings only: you MAY use <strong> and <em> tags for emphasis on key terms. No other HTML tags anywhere in your output.
2. Every bullet must contain at least one of: a specific technology name, a metric, an architectural pattern, or a problem name.
3. Truthful Extrapolation: You may rephrase existing experience to be more impactful and specify plausible concrete metrics (e.g. "~40% reduction") where the master profile implies improvement but lacks a precise number.
4. Hard Boundary: Never introduce job titles, companies, technologies, certifications, or domains absent from the master profile. The evaluator will flag any fabrication.
5. Select top 3–4 bullets per experience role. Select 1–2 projects most relevant to this JD. The resume must fit one page.
6. Do NOT include an `education` field — education is handled by Python from the master profile directly.
```

### Step 3.3 — Remove the Output Format block

Remove entirely:
```python
## Output Format
Respond with EXACTLY this structure — no extra prose before or after:

===COMPANY_NAME===
<CompanyName with no spaces>

===TAILORED_HTML===
<full tailored HTML content>
```

(Or if this was already replaced with the JSON fields description from prompt_quality Phase A, remove or replace with the new schema description from Step 3.1 above.)

### Step 3.4 — Remove `<resume_template>` from initial generation user message

In the `else` branch (initial generation mode) of the user message construction, remove:
```python
<resume_template>
{template_html}
</resume_template>
```

The template parameter is no longer sent to the model. Update the instruction at the end of the initial-mode user message from:
```
This is the initial draft generation. Please fill the template placeholders with the most relevant information for the job.
```

To:
```
Generate the resume content for this job. Select from the master profile in the system instructions.
```

### Step 3.5 — Update revision mode user message

In the `if current_html and feedback` branch, change the `<current_draft>` block. Currently it sends the raw HTML. Replace with the serialised `TailoredResumeContent` JSON:

```python
# In build_generator_prompt(), the current_html parameter will now receive
# a JSON string of TailoredResumeContent instead of raw HTML.
# Rename the parameter to current_content for clarity (update call sites too).

<current_draft>
{current_content}
</current_draft>

<feedback_to_address>
{feedback}
</feedback_to_address>

Revise the content in `<current_draft>` to address `<feedback_to_address>`. Return the same JSON schema with the updated values. Only change what the feedback requests — leave everything else identical.
```

**Note:** The parameter rename from `current_html` to `current_content` must be propagated to `gemini_client.py` call sites. See Phase 4.

---

### Step 3.6 — Update `build_evaluator_prompt()` to accept structured content

**File:** `bot/prompts.py`, function `build_evaluator_prompt()`

**Signature change:**
```python
# Old:
def build_evaluator_prompt(company_type, master_profile_json, jd, current_html, culture_signals)

# New:
def build_evaluator_prompt(company_type, master_profile_json, jd, current_content_json, culture_signals)
```

**In the user message**, replace the `<current_draft>` block:
```python
# Old — sends stripped HTML (model was doing text comprehension on mangled tags):
<current_draft>
{current_html}   ← was already stripped of tags by evaluate_resume() before being passed here
</current_draft>

# New — sends structured JSON directly:
<current_draft>
{current_content_json}
</current_draft>
```

Add a brief instruction after the `<current_draft>` block so the model knows the format:
```
The `<current_draft>` is a JSON object with fields: summary, skills[], experience[], projects[].
Reference locations in feedback using JSON field paths, e.g. experience[0].bullets[2], skills[1].items.
```

**Update the few-shot examples** in `## Output Instructions` to use JSON path references instead of prose locations:
```json
{
  "passed": false,
  "is_hallucinated": true,
  "feedback": [
    "[HALLUCINATION] skills[3].items: 'AWS SageMaker' is not in master profile — remove it",
    "[VERB] experience[0].bullets[1]: replace 'Was responsible for cache layer' → 'Engineered Redis cache layer reducing p99 latency by 35%'",
    "[KEYWORD] missing JD keyword 'Kubernetes' — add to skills[] or experience[0].bullets"
  ],
  "ats_score": 25,
  "manual_score": 55
}
```

Update all three examples (passed, borderline, failed) to use path references.

---

## Phase 4 — Update `gemini_client.py` Call Sites

### Step 4.1 — Update `start_chat_session()` (initial generation)

**Location:** `bot/gemini_client.py`, `start_chat_session()`, around lines 120–200.

**Imports to add at top of file:**
```python
from models import ResumeEvaluation, ResumeRevisions, CompanySnippet, TailoredResumeContent
from html_builder import build_resume_html
```
Remove `TailoredResumeOutput` from the import.

**Config change:**
```python
config = types.GenerateContentConfig(
    system_instruction=system_prompt,
    temperature=0.7,
    max_output_tokens=4096,   # was 32768 — structured output is ~500–800 tokens
    response_mime_type="application/json",
    response_schema=TailoredResumeContent,   # was TailoredResumeOutput
)
```

**Response parsing change** — replace the `TailoredResumeOutput.model_validate_json()` block with:
```python
content = TailoredResumeContent.model_validate_json(response.text)
company_name = content.company_name
tailored_html = build_resume_html(template_html, content)
```

Where `template_html` is the raw template string already loaded by `_load_base_resume()`. Confirm that `start_chat_session()` has access to `template_html` at this point — if not, pass it through or reload it here.

**Remove the text-marker extraction block** — any code that parses `===COMPANY_NAME===` / `===TAILORED_HTML===` markers from the response is now dead. Remove it.

---

### Step 4.2 — Update `refine_resume()` (revision loop, iterations 2+)

**Location:** `bot/gemini_client.py`, `refine_resume()`, around lines 410–490.

**Signature change:**
```python
# Old:
async def refine_resume(self, jd: str, master_profile_json: str, template_html: str,
                        company_type: str, culture_signals: str,
                        current_html: str = None, feedback: str = None, ...) -> dict:

# New: current_html is replaced with current_content (TailoredResumeContent serialised as JSON string)
async def refine_resume(self, jd: str, master_profile_json: str, template_html: str,
                        company_type: str, culture_signals: str,
                        current_content: str = None, feedback: str = None, ...) -> dict:
```

**Prompt builder call change:**
```python
# Old:
system_prompt, contents = build_generator_prompt(
    company_type, master_profile_json, template_html, jd, culture_signals,
    current_html=current_html, feedback=feedback
)

# New:
system_prompt, contents = build_generator_prompt(
    company_type, master_profile_json, template_html, jd, culture_signals,
    current_content=current_content, feedback=feedback
)
```

**Config change:**
```python
config = types.GenerateContentConfig(
    system_instruction=system_prompt,
    temperature=0.7,
    max_output_tokens=4096,   # was 32768
    response_mime_type="application/json",
    response_schema=TailoredResumeContent,
)
```

**Response parsing change:**
```python
content = TailoredResumeContent.model_validate_json(response.text)
company_name = content.company_name
tailored_html = build_resume_html(template_html, content)
```

**Return dict:** Confirm the return dict still includes `tailored_html` (the assembled HTML, not the raw JSON) and `company_name`. The rest of the bot expects HTML downstream.

---

### Step 4.3 — Update `bot.py` call site for `refine_resume()`

**File:** `bot/bot.py`, inside `tailor_process()`, in the `for iteration in range(...)` loop.

The loop currently passes `current_html` to `refine_resume()`. After Phase 4.2, it must pass `current_content` (the serialised `TailoredResumeContent`) instead. This requires storing the `TailoredResumeContent` object between iterations, not just the HTML string.

**Add before the loop** — build education HTML once and reuse across all iterations:
```python
from html_builder import build_education_html
education_html = build_education_html(master_profile_json)
# pass education_html to build_resume_html() inside start_chat_session() and refine_resume()
# via a new kwarg, or compute it inside those methods if master_profile_json is available there
```

The cleanest approach: pass `education_html` as a parameter to both `start_chat_session()` and `refine_resume()`, which then forward it to `build_resume_html()`.

**Change in the loop:**
```python
# After initial generation, store both the HTML (for evaluator) and the structured content
tailored_html = resp['tailored_html']           # assembled HTML — used by evaluator
current_content = resp['current_content_json']  # TailoredResumeContent JSON — used by next generator call

# In the generator call for iterations 2+:
resp = await client.refine_resume(
    ...,
    current_content=current_content,   # was current_html=tailored_html
    feedback=feedback_str,
    ...
)
tailored_html = resp['tailored_html']
current_content = resp['current_content_json']
```

**Required: add `current_content_json` to `refine_resume()` return dict:**
```python
return {
    'tailored_html': tailored_html,
    'company_name': company_name,
    'current_content_json': response.text,   # raw JSON string of TailoredResumeContent
    'prompt_tokens': ...,
    ...
}
```

Do the same for `start_chat_session()`.

---

### Step 4.4 — Update `evaluate_resume()` to receive structured content

**Location:** `bot/gemini_client.py`, `evaluate_resume()`, around lines 590–700.

**Signature change:**
```python
# Old:
async def evaluate_resume(self, current_html: str, master_profile_json: str, jd: str, ...) -> tuple:

# New:
async def evaluate_resume(self, current_content_json: str, master_profile_json: str, jd: str, ...) -> tuple:
```

**Remove the entire HTML stripping block** (Phase 1.2 code — now dead):
```python
# DELETE THESE LINES:
stripped_html = re.sub(r'<[^>]+>', ' ', current_html)
stripped_html = re.sub(r'[ \t]+', ' ', stripped_html)
stripped_html = re.sub(r'\n{2,}', '\n', stripped_html).strip()
```

**Update the prompt builder call:**
```python
# Old:
system_prompt, contents = build_evaluator_prompt(
    company_type, master_profile_json, jd, stripped_html, culture_signals
)

# New:
system_prompt, contents = build_evaluator_prompt(
    company_type, master_profile_json, jd, current_content_json, culture_signals
)
```

**`max_output_tokens` stays at 4096** — evaluator output (feedback JSON) is unchanged in size.

**`response_schema=ResumeEvaluation` stays unchanged** — evaluator output schema is not affected.

---

### Step 4.5 — Update `bot.py` to pass structured content to evaluator

**File:** `bot/bot.py`, inside `tailor_process()`, in the `for iteration in range(...)` loop.

The loop currently calls `evaluate_resume(current_html=tailored_html, ...)`. After Step 4.4, it must pass `current_content_json` instead.

```python
# Old:
evaluation, eval_usage = await client.evaluate_resume(
    current_html=tailored_html,
    ...
)

# New:
evaluation, eval_usage = await client.evaluate_resume(
    current_content_json=current_content,   # TailoredResumeContent JSON string from generator
    ...
)
```

`current_content` is the same variable stored from the generator response dict (see Step 4.3). No additional data flow changes needed — the variable already exists in scope by the time `evaluate_resume()` is called.

**HTML assembly moves to after the loop:**
```python
# OLD pattern (HTML assembled inside loop, passed to evaluator):
tailored_html = resp['tailored_html']   # HTML assembled inside refine_resume()
evaluation = await client.evaluate_resume(current_html=tailored_html, ...)

# NEW pattern (HTML assembled once after loop exits):
current_content = resp['current_content_json']   # JSON string, used within loop
# ... loop continues until passed or max_iterations ...

# After loop:
tailored_html = build_resume_html(template_html, TailoredResumeContent.model_validate_json(current_content), education_html)
```

**Important:** `revise_resume()` (user-triggered manual revision, separate code path) still receives assembled HTML — it is NOT affected by this change. Ensure `tailored_html` is still produced from the final `current_content` before being passed to `revise_resume()` if called.

---

## Phase 5 — Cleanup

### Step 5.1 — Remove HTML stripping code from `evaluate_resume()`

After Step 4.4, the three `re.sub` lines are dead code. Confirm they are gone:
```python
# These must NOT exist anywhere in evaluate_resume() after migration:
stripped_html = re.sub(r'<[^>]+>', ' ', current_html)
stripped_html = re.sub(r'[ \t]+', ' ', stripped_html)
stripped_html = re.sub(r'\n{2,}', '\n', stripped_html).strip()
```

Also confirm `import re` is still needed elsewhere in `gemini_client.py` before removing it. If it is only used for the stripping block, remove the import too.

---

### Step 5.2 — Remove `TailoredResumeOutput` from `models.py`

Once all call sites are confirmed working, delete:
```python
class TailoredResumeOutput(BaseModel):
    company_name: str = Field(...)
    tailored_html: str = Field(...)
```

Grep for any remaining references: `grep -r "TailoredResumeOutput" bot/`

### Step 5.3 — Remove `template_html` from `build_generator_prompt()` signature

The template is no longer sent to the model. The parameter `template_html` in `build_generator_prompt()` is now unused. Remove it from:
- `build_generator_prompt()` signature in `prompts.py`
- All call sites in `gemini_client.py`

### Step 5.4 — Update `max_output_tokens` comments in implementation plan files

Update [token_efficiency_implementation_plan.md](./token_efficiency_implementation_plan.md) and [prompt_quality_implementation_plan.md](./prompt_quality_implementation_plan.md) — Phase D token values for generators (previously 32,768) are now 4,096.

---

## Phase 6 — Validation

Run these checks in order after all phases are deployed:

### 6.1 — Unit test the assembler
Run the test script from Phase 2. Confirms `build_resume_html()` produces valid HTML with all placeholders replaced.

### 6.2 — Visual diff a generated resume
Run one full `/tailor` session. Open the generated HTML in a browser. Compare visually against a pre-migration generated resume (e.g. `generated/VolaFinance/PurveshGandhi_Resume_VolaFinance.html`). Check:
- Summary section appears if model included it
- Skills rows are formatted correctly (bold category, comma-separated items)
- Experience bullets render with correct `<strong>` emphasis
- Projects show title, tech stack, date, bullets
- Education section is intact

### 6.3 — Token count comparison
Query Supabase after 3+ sessions:
```sql
SELECT
    feature,
    ROUND(AVG(prompt_tokens)) AS avg_input,
    ROUND(AVG(completion_tokens)) AS avg_output,
    ROUND(AVG(completion_tokens) * 12.0 / 1000000, 5) AS avg_output_cost_usd
FROM llm_requests
WHERE feature IN ('tailor_generator')
GROUP BY feature;
```
Expect `avg_output` to drop from ~7,000–9,000 to ~500–800 tokens.

### 6.4 — Evaluator receives structured content and produces JSON path feedback
Inspect `agent_traces` where `agent_role = 'evaluator'`. Confirm:
- `prompt_text` contains the `TailoredResumeContent` JSON in the `<current_draft>` block — not HTML
- `raw_response` feedback items reference JSON paths (`experience[0].bullets[2]`, `skills[3].items`) rather than prose locations (`TechCorp bullet 2`)
- `prompt_tokens` for `critic_evaluator` drops from ~2,000–3,000 to ~500–800

### 6.5 — HTML assembly happens once after loop
Confirm in `bot.py` logs that `build_resume_html()` is called once per session, not once per iteration. The simplest check: add a temporary `logger.info("Assembling HTML")` before the `build_resume_html()` call and verify it appears exactly once in the log for a 2-iteration session.

### 6.6 — `revise_resume()` (manual user revision) still works
This function is NOT modified in this plan. It still receives and returns HTML with targeted edits. Run one manual revision to confirm it still works end-to-end.

---

## Implementation Sequence

```
Phase 1 (NOW)    Add Pydantic models to models.py (TailoredResumeContent, SkillRow, ExperienceRole, Project)
Phase 2 (NOW)    Write bot/html_builder.py + run unit test
        ↓  [unit test must pass before continuing]
        ↓
Phase 3          Update prompts.py
                   3.1–3.5  Generator prompt: task, rules, remove template, revision mode param rename
                   3.6      Evaluator prompt: structured content input, JSON path feedback examples
        ↓
Phase 4          Update gemini_client.py + bot.py
                   4.1  start_chat_session()  — schema, parsing, education_html
                   4.2  refine_resume()       — schema, current_content param, parsing
                   4.3  bot.py loop           — store current_content, pass to refine_resume()
                   4.4  evaluate_resume()     — remove HTML stripping, receive current_content_json
                   4.5  bot.py loop           — pass current_content to evaluate_resume(); move HTML assembly after loop
        ↓
        [Deploy — run 3+ sessions]
        ↓
Phase 6          Validate
                   6.1  Unit test assembler
                   6.2  Visual diff generated resume in browser
                   6.3  Token counts: generator output drops ~90%
                   6.4  Evaluator input drops ~60–70%; feedback uses JSON path refs
                   6.5  HTML assembly logged once per session
                   6.6  revise_resume() manual revision unaffected
        ↓
Phase 5          Cleanup
                   5.1  Remove re.sub HTML stripping from evaluate_resume()
                   5.2  Remove TailoredResumeOutput from models.py
                   5.3  Remove template_html param from build_generator_prompt()
                   5.4  Update max_output_tokens in sibling plan files
```

**Critical order constraint:** Phases 4.4 and 4.5 (evaluator changes) depend on 4.1–4.3 (generator changes) being deployed first, because the evaluator now consumes the `current_content_json` that the generator produces. Do not deploy the evaluator change without the generator change already live.
