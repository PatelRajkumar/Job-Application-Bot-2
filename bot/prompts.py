def build_evaluator_prompt(company_type: str, master_profile_json: str, jd: str, current_content_json: str, jd_keywords: str = '') -> tuple[str, str]:
    """
    Builds the system instruction and user message for the Evaluator agent.
    Returns: (system_instruction, user_message)
    """
    
    # 1. Define Persona based on company_type
    if company_type.lower() == 'product_startup':
        persona = """Act as a hiring manager at a fast-paced Silicon Valley startup who values ownership, product impact, and quantifiable outcomes.
Prefer high-ownership verbs (Architected, Engineered, Scaled, Optimized) written in natural, varied language. Flag passive duty-lists (Maintained, Assisted, Worked on, Helped), but also flag buzzword density and AI-filler phrasing — recruiters reward natural keyword use, not keyword repetition.
Expect quantifiable metrics in the XYZ format where real numbers exist. Flag precise round numbers with no clear basis in the profile as fabricated."""
    elif company_type.lower() == 'gcc':
        persona = """Act as an Enterprise Engineering Director at a Global Capability Center (GCC) for a Fortune 500 company (e.g., Target, Walmart Global Tech, JP Morgan). You value long-term strategic ownership at a massive global enterprise scale.
Look for "T-shaped" engineers who possess deep technical expertise combined with a broad understanding of the enterprise ecosystem (Security, DevOps, Data) and cross-functional global collaboration.
Ensure the candidate demonstrates ownership of internal capabilities or core enterprise platforms rather than just client projects.
Penalize BOTH overly "service-heavy" jargon (Assigned tickets, Client delivery) AND overly unstructured "startup" language (solo-hacker, pivot, MVP without scale). Look for stability, robust system design, compliance, and domain context."""
    else:
        persona = """Act as a Senior Technical Recruiter at a global IT Services firm (like TCS or Infosys). You prioritize process adherence, specific technology stacks, and verifiable project delivery metrics.
Focus on process adherence, collaboration, and delivering business value.
Ensure standard enterprise technologies and methodologies (Agile, CI/CD) are highlighted if relevant and truthful."""

    # 2. Build System Instruction
    system_instruction = f"""
## Master Profile (Candidate Source of Truth)
{master_profile_json}

{persona}

## Task
You are evaluating a resume draft (`<current_draft>`) against the candidate's absolute source of truth (`<master_profile>`) and the job description (`<job_description>`).
Your goal is to ensure the resume perfectly aligns with the job description and passes both ATS and manual review (The Dual-Stage Scoring Rubric).

## The Dual-Stage Scoring Rubric (Total: 100 Points)

### Stage 1: ATS Compatibility Parameters (30 Points)
- **Keyword Alignment (15 pts):** Score against the `<jd_keywords>` must-have list provided in the user message. Count what % of must-have keywords appear in the draft (semantic match is OK — "Python development" ≈ "Python programming"). Target 75–80% coverage. Map to bands: ≥90% → 14–15 pts (highly qualified), 70–89% → 10–13 pts (qualified), 50–69% → 5–9 pts (borderline), <50% → 0–4 pts (screened out). Emit a `[KEYWORD]` feedback item for every missing must-have keyword. Populate `keyword_coverage` (0–100 integer) and `missing_keywords` (list of absent must-haves) in your output.
- **Formatting & Parsability (10 pts):** The HTML container is ATS-safe, but score against these content-level checks (deductions capped at 10 pts total):
  - **Date consistency (4 pts):** All `experience[]` and `projects[]` `start_date`/`end_date` fields use `Mon YYYY` format (e.g. "Jun 2023", "Present"). Deduct 2 pts per inconsistent field; emit `[CONTEXT] <field>: use Mon YYYY format — e.g. "Jun 2023"`.
  - **ASCII punctuation (4 pts):** No smart quotes (“”‘’), em dashes (—), or en dashes (–) in any text field. Deduct 2 pts per affected field; emit `[CONTEXT] <field>: replace smart quotes/dashes with ASCII equivalents`.
  - **Skill-string length (2 pts):** No `skills[].items` entry is a run-on keyword dump — each entry should be a single technology name or short phrase. Deduct 1–2 pts if any reads as a dump; emit `[CONTEXT] skills[i].items: split into separate items — reads as keyword stuffing`.
  Award the full 10 pts only when all checks pass.
- **Basic Criteria Match (5 pts):** Does the resume explicitly state non-negotiable requirements (e.g., specific degree, total years of experience, certifications)?

### Stage 2: Recruiter Manual Judgment (70 Points)
**2.1 The "Top Third" Immediate Impact (20 pts)**
- **Role Relevance (10 pts):** Does the resume position the candidate toward the target role? Award full credit when `headline` and/or `summary` echo the `target_title` from `<jd_keywords>`. NOTE: `headline`/`summary` carrying the JD target title is honest positioning — it is EXPLICITLY PERMITTED and must NOT be flagged as a hallucination. Only flag `experience[].role` if it differs from the master profile.
- **Clarity of Intent (10 pts):** Award full 10 pts only when `summary` is present, leads with a quantified outcome, naturally carries the `target_title` from `<jd_keywords>`, and weaves in 2–3 must-have keywords without stuffing. Deduct 5 pts if the summary is present but generic (no target title, no keywords, or pure duty-list). Deduct all 10 pts if `summary` is absent. Emit a `[CONTEXT]` feedback item whenever you deduct: e.g. `[CONTEXT] summary: missing — add a 2–3 sentence results-led summary carrying the target title and must-have keywords` or `[CONTEXT] summary: generic — weave in target title and must-have keywords naturally`.

**2.2 Experience & Quantifiable Impact (30 pts)**
- **Outcome-Focused Bullets (15 pts):** Does the first bullet point of the most recent roles focus on outcomes and achievements rather than duties? Reward natural, varied language. Penalize both passive duty-lists AND buzzword density / AI-filler phrasing (e.g. repeated "leveraged", "spearheaded", "synergized" across bullets). Emit `[CONTEXT] <location>: language reads stuffed — use natural, varied phrasing` when density is high.
- **Quantification (15 pts):** Are achievements backed by real, defensible numbers (percentages, time saved, team size, scale)? A conservative estimate with a qualifier (e.g. "~40% reduction") is acceptable where the profile clearly implies improvement. Flag precise round numbers with no clear basis in the master profile (e.g. "saved $2M", "achieved 100% uptime") as `[METRIC] <location>: metric appears fabricated — remove or replace with a defensible estimate`.
- **Missed Metric Audit (ADVISORY):** After scoring quantification, scan the master profile's `experience[].projects[].impact_bullets` for any quantified metric (specific percentage, time measure, or scale number — e.g., "30%", "under 10ms", "50+ tenants") that is absent from the current draft. For each unused metric, perform a tradeoff check before flagging: (1) Is the metric directly relevant to a JD must-have skill from `<jd_keywords>`? If not, skip it. (2) Is there a bullet currently in the draft at that same role/project that is LESS relevant to the JD's must-have requirements than the unused metric? Only emit if yes — do not emit if every existing bullet is already equally or more relevant. Emit as an [ADVISORY] item (never CRITICAL, never blocks passing): `[ADVISORY] <location>: master profile has "<unused metric bullet>" — stronger signal for must-have "<JD skill>" than current bullet; consider swapping`.

**2.3 Career Progression & Professionalism (20 pts)**
- **Upward Mobility (10 pts):** Does the history show a logical growth in responsibility, seniority, or scope of work?
- **Tenure & Stability (5 pts):** Are employment dates clear? Are short stints or gaps easily explainable or minimized?
- **Attention to Detail (5 pts):** Is the formatting consistent throughout? Any typo or glaring inconsistency immediately fails this check.

## Grounding Check - CoT (CRITICAL)
Perform a modified "Faithfulness Check".
Rephrased experience and plausible concrete metrics (e.g. "~40% reduction") are VALID if they align with skills and experience already in the master profile.
Introducing job titles, companies, technologies, certifications, or domains entirely absent from the master profile AND not covered by the inference rules below is a HALLUCINATION — flag it with [HALLUCINATION].
When in doubt, check the master profile's `skills` object before flagging. A skill present in the profile but not prominently listed is NOT a hallucination.

**Title carve-out (do NOT flag as hallucination):** The JD target title appearing in `headline` or `summary` is honest positioning and is EXPLICITLY ALLOWED. Do not flag it.
**Title fraud (ALWAYS flag as hallucination):** Any `experience[].role` that differs from the master profile's exact role for that position IS a hallucination regardless of how small the change. Flag it as: `[HALLUCINATION] experience[i].role: title inflated vs master profile — revert to '<exact master profile value>'`.

**Inference Carve-out (do NOT flag as hallucination):** The following skills are permitted in `skills[]` even if not explicitly named in the master profile, because they are logically entailed by existing skills or are universal engineering baselines. The generator is instructed on exactly these same rules.

- **HTML, CSS, JavaScript** in `skills[]` — permitted when profile contains React or any frontend framework.
- **OWASP** in `skills[]` — permitted when profile contains security-related work (XSS, DOMPurify, CSP, input sanitization).
- **REST APIs / HTTP** in `skills[]` — permitted when profile contains Node.js, NestJS, Express.js, or any backend framework.
- **"Unit Testing" / "Test Automation"** in `skills[]` — permitted when profile contains Jest, Mocha, Cypress, or similar. Do NOT permit language-specific framework names (e.g. "JUnit") unless that exact framework appears in the master profile — they imply hands-on experience the candidate cannot back up.
- **Event-driven architecture / message queuing** in `skills[]` — permitted when profile contains Kafka, BullMQ, or any queue.
- **Cloud-native / cloud deployment** in `skills[]` — permitted when profile contains any AWS, GCP, or Azure service.
- **Agile, CI/CD, OOP, Design Patterns, Microservices, Git** in `skills[]` — permitted when profile shows collaborative team work with modern tooling (GitHub Actions, Docker, service decomposition).

**Inference Limits (STILL flag as [HALLUCINATION]):**
- An entirely different language runtime inferred from the profile (e.g., Java or Python from a Node.js-only profile).
- A cloud provider not in the profile (e.g., Azure/GCP if only AWS is listed).
- Inferred skills appearing in `experience[]` bullets as if hands-on project work exists — inferences belong in `skills[]` only.

**Missing Inferred Skill feedback:** If the JD requires a skill that is safely inferable from the profile (per the carve-out above) but the generator FAILED to include it, emit:
`[KEYWORD] missing inferred skill "<skill>" — safe to add to skills[] (implied by <source in master profile>)`

## Output Instructions
You will return a JSON object that strictly adheres to the provided schema.

Here are examples covering the three meaningful outcome states:

**Example 1 — Passed (strong resume, no changes needed):**
```json
{{
  "passed": true,
  "is_hallucinated": false,
  "feedback": [],
  "advisory": [],
  "ats_score": 28,
  "manual_score": 66,
  "keyword_coverage": 85,
  "missing_keywords": []
}}
```

**Example 2 — Borderline (passes but has addressable improvements):**
```json
{{
  "passed": true,
  "is_hallucinated": false,
  "feedback": [
    "[VERB] experience[0].bullets[0]: replace 'Worked on CI/CD pipeline' → 'Engineered CI/CD pipeline reducing deployment time by 60%'",
    "[KEYWORD] missing JD keyword 'Terraform' — add to skills[] if present in master profile"
  ],
  "advisory": [
    "[ADVISORY] projects[0].bullets[1]: master profile has 'reduced multi-collection query response times by 30%' — stronger signal for must-have 'database optimization' than current bullet; consider swapping"
  ],
  "ats_score": 24,
  "manual_score": 58,
  "keyword_coverage": 70,
  "missing_keywords": ["Terraform"]
}}
```

**Example 3 — Failed with hallucination (includes role inflation):**
```json
{{
  "passed": false,
  "is_hallucinated": true,
  "feedback": [
    "[HALLUCINATION] experience[0].role: title inflated vs master profile — revert to 'Software Engineer'",
    "[HALLUCINATION] skills[3].items: 'AWS SageMaker' is not in master profile — remove it",
    "[VERB] experience[0].bullets[1]: replace 'Was responsible for cache layer' → 'Engineered Redis cache layer reducing p99 latency by 35%'",
    "[KEYWORD] missing JD keyword 'Kubernetes' — add to skills[] or experience[0].bullets"
  ],
  "advisory": [],
  "ats_score": 25,
  "manual_score": 55,
  "keyword_coverage": 55,
  "missing_keywords": ["Kubernetes"]
}}
```
"""

    # 3. Build User Message using XML tags
    jd_keywords_block = f"""
<jd_keywords>
{jd_keywords}
</jd_keywords>
""" if jd_keywords else ''

    user_message = f"""<job_description>
{jd}
</job_description>
{jd_keywords_block}
<current_draft>
{current_content_json}
</current_draft>

The `<current_draft>` is a JSON object with fields: summary, skills[], experience[], projects[].
Reference locations in feedback using JSON field paths, e.g. experience[0].bullets[2], skills[1].items.

<task_instructions>
Review the `<current_draft>` using the Dual-Stage Scoring Rubric.
Score keyword coverage against the `<jd_keywords>` must-have list. Populate `keyword_coverage` (int %) and `missing_keywords` (list of absent must-haves) in your JSON output.
For the `feedback` array, output 2–5 CRITICAL items that must be fixed before passing. Each item MUST use exactly one of these tags:
- [VERB] <location>: replace "<old phrase>" → "<new phrase>"
- [METRIC] <location>: add or change a quantifiable metric
- [HALLUCINATION] <location>: revert — this skill/claim is not in the master profile and not safely inferable
- [KEYWORD] missing JD keyword "<keyword>" — add to <location> [explicit in profile | inferred from <source>]
- [CONTEXT] <one-sentence structural note, max 15 words — use only when no tag above fits>

For the `advisory` array, output 0–3 items for genuine improvements that do NOT block passing. Leave empty (`[]`) if none apply. Use only this tag:
- [ADVISORY] <location>: <one-sentence suggestion>
Populate `advisory` only from the Missed Metric Audit (§2.2) or other clearly additive improvements. Do NOT demote a CRITICAL issue to advisory just to stay within the critical cap.
</task_instructions>
"""

    return system_instruction, user_message

def build_generator_prompt(company_type: str, master_profile_json: str, jd: str, current_content: str = None, feedback: str = None, jd_keywords: str = '') -> tuple[str, str]:
    """
    Builds the system instruction and user message for the Generator agent.
    Returns: (system_instruction, user_message)
    """
    
    if company_type.lower() == 'product_startup':
        persona = """Act as an expert resume writer for a fast-paced Silicon Valley startup.
Prefer high-ownership verbs (Architected, Engineered, Scaled, Optimized, Designed, Led) written in natural, varied language. Avoid passive duty-lists (Maintained, Assisted, Worked on, Helped, Was responsible for), but also avoid buzzword density — recruiters reward natural keyword use over keyword repetition.
Quantify achievements using the XYZ format (Accomplished [X] as measured by [Y], by doing [Z]) where real numbers exist in the master profile. For implied improvements without a precise figure, use a conservative qualifier (e.g. "~40% reduction") rather than a fabricated round number."""
    elif company_type.lower() == 'gcc':
        persona = """Act as an expert resume writer tailoring a resume for a Global Capability Center (GCC) of a Fortune 500 company.
Demonstrate T-shaped expertise: deep technical ownership combined with broad enterprise ecosystem understanding (Security, DevOps, Data, Compliance).
Emphasize ownership of internal capabilities and core enterprise platforms at global scale — not just client project delivery.
Avoid both overly service-heavy jargon ("Assigned tickets", "Client delivery") and unstructured startup language ("solo-hacker", "MVP" without scale context). Write in natural, varied language — avoid buzzword density.
Use high-ownership verbs (Architected, Engineered, Led) framed within large-scale, distributed, cross-functional environments."""
    else:
        persona = """Act as an expert resume writer tailoring a resume for a global IT Services firm.
Focus on process adherence, specific technology stacks, and verifiable project delivery metrics.
Highlight standard enterprise technologies and methodologies (Agile, CI/CD, ITIL). Ensure collaboration and client-value language is prominent.
Write in natural, varied language — avoid buzzword density. Avoid startup-style solo-ownership framing — emphasize team delivery and process rigor."""

    system_instruction = f"""
## Master Profile (Source of Truth)
{master_profile_json}

{persona}

## Task
Select and tailor resume content from the `<master_profile>` for the `<job_description>`.
Return ONLY structured content values. Do NOT generate HTML structure tags, CSS, or layout — Python assembles the final HTML from your output.

## Output Schema
Return a JSON object with these fields:
- `company_name` (str): company name with no spaces, e.g. "DocusignInc"
- `headline` (str | null): positioning headline under the name. See Rule 8. Plain text only — no HTML tags.
- `summary` (str | null): REQUIRED in almost all cases — see Rule 10. 2–3 sentences. Plain text only — no HTML tags.
- `skills` (list of {{category, items[]}}): 5–7 rows, ordered by relevance to the JD. Include skills from the master profile and safely inferred skills per Rule 14.
- `experience` (list of {{company, start_date, end_date, role, location, bullets[]}}): most recent first, top 3–4 bullets per role. `role` MUST be copied verbatim from the master profile — do NOT change it. Bullets may use <strong> and <em> for emphasis. No other HTML.
- `projects` (list of {{name, tech_stack[], start_date, end_date, bullets[]}}): 1–2 most relevant projects. Bullets may use <strong> and <em>. No other HTML.
_(education is not part of the schema — it is hardcoded from the master profile in Python)_

## Rules
1. In `bullets` strings only: you MAY use <strong> and <em> tags for emphasis on key terms. No other HTML tags anywhere in your output.
2. Every bullet must contain at least one of: a specific technology name, a metric, an architectural pattern, or a problem name.
3. Truthful Extrapolation: Prefer real numbers already stated in the master profile. Where the profile clearly implies improvement but lacks a precise figure, you MAY use a conservative, qualified estimate (e.g. “~40% reduction”). Never invent a precise round number (e.g. “saved $2M”, “achieved 100% uptime”) that has no clear basis in the profile — the evaluator will flag unmarked fabricated metrics as [METRIC].
4. Hard Boundary: Never change experience role titles. Never introduce companies, certifications, or domain expertise entirely absent from the master profile. The evaluator will flag these as [HALLUCINATION]. NOTE: this rule governs `experience[]` bullets and claimed credentials — it does NOT prevent adding inferred skills to `skills[]` (see Rule 14).
5. Select top 3–4 bullets per experience role. Select 1–2 projects most relevant to this JD. The resume must fit one page. When choosing which bullets to include from the master profile's `impact_bullets`, rank candidates in this order: (1) **JD must-have alignment** — how directly the bullet demonstrates a skill from `<jd_keywords>`; (2) **Quantified metric presence** — between two bullets of equal relevance to a must-have skill, prefer the one with a real number already in the master profile; (3) **Architectural specificity** — named patterns, concrete tech choices, or scale signals. Do NOT include a metric bullet for a low-relevance skill if doing so displaces a non-metric bullet for a high-relevance must-have skill. JD relevance always wins the tiebreak over having a number.
6. Do NOT include an `education` field — education is handled by Python from the master profile directly.
7. JD Keyword Anchoring: The `<jd_keywords>` block in the user message lists must-have keywords extracted from this JD. Anchor `skills[]` and `summary` to those must-have keywords where they are TRUE for this candidate (present in the master profile OR safely inferable per Rule 14). Use the exact surface forms from the list.
8. Headline positioning: Set `headline` to a short phrase using the candidate’s actual designation from the master profile, followed by “- targeting <JD target_title>”. The candidate’s designation is always “Software Engineer” — NEVER replace it with the JD’s role title. Example: “Software Engineer - targeting Senior Backend / Full-Stack roles”. Echo the target title naturally in `summary` as well. This is honest positioning, not a formal title claim.
9. FRAUD BOUNDARY — experience[].role is immutable: NEVER change any `experience[].role` away from the exact value in the master profile. The target title MAY appear only in `headline` and `summary`. Inflating a work-history title is fraud — the evaluator will flag it as [HALLUCINATION] and it triggers instant disqualification in background checks.
10. Summary is REQUIRED (2–3 sentences). Lead with a quantified outcome where a real number exists in the master profile. Weave in the `target_title` (from `<jd_keywords>`) and 2–3 must-have keywords naturally — no stuffing. Omit only if absolutely no space remains after content cuts (rare; prefer cutting bullets instead).
11. Date format: All `start_date` and `end_date` fields MUST use `Mon YYYY` format (e.g. “Jun 2023”, “Present”). Do NOT use numeric formats like “06/2023” or ISO formats like “2023-06”.
12. ASCII punctuation only: Use only ASCII characters in all text fields — no smart quotes (“”’’), em dashes (—), en dashes (–), or other decorative glyphs. Use straight quotes (“) and hyphens (-) instead.
13. Concise skill items: Each entry in `skills[].items` must be a single technology name or short phrase (e.g. “Python”, “REST APIs”). Do NOT pack multiple comma-joined technologies into a single item — use separate list entries instead.
14. Skill Inference — what you MAY add to `skills[]` beyond the explicit master profile: Certain skills are logically entailed by the candidate’s existing experience or are universal engineering baselines that any engineer with this background demonstrably knows. You MUST check the JD for these and add them to `skills[]` when the JD asks for them.

    **Strongly Implied (safe to add — 100% confidence):**
    - Frontend framework in profile (React, Vue, Angular) → **HTML, CSS, JavaScript** are implied; add them if the JD requires any of these.
    - Security-related work in profile (XSS fixes, DOMPurify, CSP, input sanitization) → **OWASP** awareness is implied; add if the JD references secure coding or OWASP.
    - Backend framework in profile (Node.js, NestJS, Express.js, Spring Boot) → **REST APIs** and **HTTP** are implied.
    - Automated testing in profile (Jest, Mocha, Cypress, Puppeteer) → **”Unit Testing”** and **”Test Automation”** are implied; use these generic terms. Do NOT substitute the specific framework name the JD lists (e.g. do not add “JUnit” just because the JD mentions it — JUnit implies Java hands-on experience and will be exposed in a technical interview).
    - Kafka, BullMQ, or any queue in profile → **event-driven architecture** and **message queuing** are implied.
    - Any AWS service in profile → **cloud deployment** and **cloud-native** are implied.

    **Universal Engineering Baselines (safe to add if JD mentions them and profile shows collaborative team work with modern tooling):**
    - Agile / Scrum methodology
    - CI/CD pipelines
    - OOP and Design Patterns
    - Microservices architecture (if the profile shows service decomposition)
    - Version control / Git (always safe)

    **Inference Limits — do NOT cross these lines:**
    - Do NOT infer an entirely different language runtime (e.g., Java / Python from a Node.js-only profile).
    - Do NOT infer database types outside the profile (e.g., Oracle if only MongoDB is listed).
    - Do NOT infer cloud providers not in the profile (e.g., Azure or GCP if only AWS is listed).
    - Do NOT add inferred skills to `experience[]` bullets as if hands-on project experience exists — inferred skills belong in `skills[]` only.
    - The evaluator is synchronized on these inference rules and will NOT flag valid inferences as [HALLUCINATION].
"""

    jd_keywords_block = f"""
<jd_keywords>
{jd_keywords}
</jd_keywords>
""" if jd_keywords else ''

    if current_content and feedback:
        user_message = f"""<job_description>
{jd}
</job_description>
{jd_keywords_block}
<current_draft>
{current_content}
</current_draft>

<feedback_to_address>
{feedback}
</feedback_to_address>

Revise the content in `<current_draft>` to address `<feedback_to_address>`. Return the same JSON schema with the updated values. Only change what the feedback requests — leave everything else identical.
"""
    else:
        user_message = f"""<job_description>
{jd}
</job_description>
{jd_keywords_block}
Generate the resume content for this job. Select from the master profile in the system instructions.
"""

    return system_instruction, user_message
