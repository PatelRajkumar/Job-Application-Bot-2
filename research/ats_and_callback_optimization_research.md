# ATS Scoring & Callback Optimization — Market and Systems Research

**Date:** 2026-06-26
**Author:** Research phase for the resume-tailoring bot
**Problem statement:** Generated resumes look good, but callback volume is low. This document researches (a) how popular ATS systems actually parse and score, (b) what comparable, well-reputed products do, (c) what *actually* drives callbacks, and (d) what our current implementation does right and wrong. It also fact-checks the claims made earlier in the design conversation.

---

## 0. How to read this document (source-quality note)

The resume/ATS space is flooded with SEO content marketing from tools trying to sell "ATS-optimized" templates. Much of it recycles the same unsourced statistics. I have separated sources into tiers and weighted conclusions accordingly:

- **Tier 1 — Authoritative / primary:** Harvard Business School research, academic papers, named studies with disclosed methodology (ResumeGo, The Ladders eye-tracking), SHRM surveys, and the canonical source for a technique (e.g., Laszlo Bock / Google for the XYZ formula).
- **Tier 2 — Vendor / practitioner, useful but interested:** Jobscan, Teal, Rezi, Resume Worded blogs and product docs. Reliable about *their own products and tests*; their *general statistics* (e.g., "40% more callbacks") are marketing and are flagged as such.
- **Tier 3 — Generic SEO blogs:** Used only for corroboration of consensus points, never as a sole source for a claim.

**Headline caveat that reframes the whole problem:** The popular narrative — "an ATS robot auto-rejects 75% of resumes" — is essentially a myth (see §2). The low-callback problem is **overwhelmingly a relevance, ranking, and volume problem, not a parsing problem.** Our tooling has been optimizing heavily for the parsing problem (which we have largely already solved) and under-investing in the relevance/ranking problem.

---

## 1. How popular ATS systems actually parse and score

### 1.1 Two distinct stages, often conflated

1. **Parsing** — the system converts the uploaded file into structured fields (contact, work history, education, skills). Anything the parser cannot cleanly read simply *does not exist* downstream. Greenhouse's AI summary and match score are built entirely from parsed fields — "anything it can't read doesn't get scored." [Greenhouse parsing behavior]
2. **Scoring / ranking** — *some* systems compute a relevance score against the job description; many do not, and instead just make the parsed data searchable for a recruiter.

### 1.2 The systems behave very differently

| System | Scoring behavior | Matching style |
|---|---|---|
| **Taleo** (legacy, strictest) | Keyword-density ranking; paste-to-form flow | **Strict literal/exact** keyword matching |
| **Greenhouse** | **Does not auto-score** to rank/reject; builds a structured profile + scorecard for humans | Parser quality is everything; AI summary depends on clean extraction |
| **Workday** | Multi-layer AI screening | **Exact + semantic** term coverage |
| **iCIMS** | Talent Cloud NLP scoring | Semantic / skills-graph |
| **Lever** | CRM-style, recruiter-driven search | Search/filter driven |

Workday's screening is described as having ~5 evaluation layers: (1) keyword match — exact and semantic coverage vs the JD; (2) content quality — quantification density, action-verb strength, specificity; (3) format safety — whether the structure survives parsing; (4) intent fit — trajectory vs requisition; (5) recency — whether the most relevant experience appears near the top, where parsing weights it higher. *(Source is a vendor/tool blog synthesizing Workday behavior — treat the exact "5 layers" as indicative, not gospel — but it aligns with the academic and recruiter-survey consensus.)*

### 1.3 Exact vs semantic matching has shifted (2024–2026)

- 2020–2023: predominantly **exact** keyword matching.
- 2024–2026: major platforms (Workday's AI layer, iCIMS, Greenhouse scoring) added **semantic matching** built on NLP trained on millions of resume/JD pairs. They now understand that "Python programming," "Python development," and "Python scripting" are the same competency, and increasingly use **skills-graph matching** that maps relationships between skills, roles, and outcomes.
- **Practical implication:** exact keyword string-matching still matters most for **legacy systems (Taleo) and for recruiter Boolean search** (see §2.3), but pure keyword-stuffing is decreasingly effective and is now actively flagged by "red-flag detection for repeated keywords or AI-generated filler."

### 1.4 Keyword *placement* is weighted

Multiple sources agree: keywords in the **Professional Summary** and **Skills** section carry **more scoring weight** than the same keyword buried in a 10-year-old bullet. Recency and top-of-document position are weighted higher.

### 1.5 General relevance-score bands (where scoring exists)

Indicative thresholds cited across sources: **90–100% = "highly qualified," 70–89% = "qualified," 50–69% = "borderline," <50% = effectively screened out.** This lines up with Jobscan's recommended **75–80% match-rate target** (§4.1).

---

## 2. Fact-check: the ATS myths (and the *real* bottleneck)

### 2.1 The "75% auto-rejected" statistic is a myth

- The "75% of resumes are rejected by an ATS before a human sees them" figure traces to a **2012 sales pitch by Preptel**, a resume-optimization vendor that **went out of business in 2013**. No methodology was ever published. [The Interview Guys; multiple corroborating]
- Career consultant **Christine Assaf** searched Google Scholar and found **zero** academic research supporting the number.
- A 2025 survey found 68% of recruiters first heard the "75%" claim *from job seekers on social media*; 20% blamed career coaches recycling outdated advice. It is repeated largely **to sell "ATS-optimized" templates and services.**

### 2.2 ATS rarely auto-rejects on content — humans do the reviewing

Primary data from an **Enhancv survey of 25 U.S. recruiters** (companies from 120 to 50,000+ employees, across tech/healthcare/finance/etc.):

- **92% (23/25)** do **not** use auto-rejection based on formatting, content, or design.
- **8% (2/25)** configure auto-rejection — and only on strict criteria like "match < 75%" or "fewer than N required skills."
- **100%** use **knockout questions** — eligibility gates like work authorization/visa sponsorship, required degree, location. **These are the real automated rejections.**
- Recruiter Jan Tegze: **"90–95% or more of all applications are reviewed by a human."**

The Harvard Business School **"Hidden Workers: Untapped Talent" (2021, Joseph Fuller, Manjari Raman, Eva Sage-Gavin, Kristen Hines)** is the authoritative counter-nuance: rigid *human-configured* filters (continuous-employment requirements, narrow credential definitions, exact-match criteria) screen out **~27M capable "hidden workers"** in the US. The biggest single screen-out factor was **employment gaps of 6+ months** — again, a human-set knockout, not a mysterious robot. So: filters do harm qualified people, but they are **configured by recruiters**, not an autonomous AI rejecting on resume aesthetics.

### 2.3 The real bottleneck: search, ranking, and volume

This is the most important finding for our problem. When a posting pulls 250–4,000 applicants in days, the recruiter does **not** read them all. They:

1. **Search the parsed ATS database** with Boolean queries for the **titles and skills** the role needs,
2. review the **first cluster** that comes back,
3. shortlist ~10–15, and **stop.**

**Consequence:** You are not usually "rejected." You are **out-ranked or never surfaced** because your **title and core skill keywords didn't match the recruiter's search**, or because you applied late in a high-volume window. This means **exact title/skill matching and application timing** matter more for callbacks than passing a parsing gate we've already cleared.

### 2.4 Format/parsing fact-check (PDF vs DOCX, what actually breaks)

- **Both PDF and DOCX work in modern ATS** *if they contain a real text layer.* Greenhouse, Lever, Workday, and iCIMS all parse clean, text-based PDFs reliably.
- DOCX parses marginally more reliably on average across *all* (including legacy) engines; PDF is preferred for preserving human-facing visual quality. The deciding factor is **a real text layer**, not the container format.
- **What actually breaks parsing** (consistent across sources, including Jobscan's own tests):
  - **Multi-column / sidebar layouts** — the #1 cause of failure. Parser reads straight across columns and scrambles content. Multi-column templates drop **skills-section extraction accuracy to ~46%, vs ~93% for single-column.**
  - **Tables and text boxes** — reading order is lost or skipped entirely.
  - **Content in headers/footers** — frequently ignored; never put name/contact/skills there.
  - **Scanned images** — no text layer = invisible.
  - **Exotic Unicode/special characters** — arrows, decorative bullets, smart quotes, em/en dashes can garble.
- **Validation technique everyone recommends:** the **copy-paste test** — select-all in the PDF, paste into plain text (Notepad), and confirm it extracts cleanly and in the right order.

---

## 3. What actually drives callbacks (evidence-ranked)

### 3.1 Recruiter attention is ~7 seconds (Tier 1)

**The Ladders eye-tracking study** (2018 update of the 2012 study; 30 professional recruiters, eye-tracking hardware, 10 weeks): average **7.4 seconds** of initial scan. Recruiters skim for **layout, job titles, text flow, and keywords.** Recommendations from the study itself:

- **Don't cram** the page full of text.
- **Bold** section headers and job titles to anchor the eye.
- Use **short, declarative accomplishment statements**, not paragraphs.
- Use **keywords in context**, not stuffed.

### 3.2 What recruiters say they prioritize (Tier 1 — Enhancv 25-recruiter survey)

Ranked by % of recruiters citing it:

1. Clear, skimmable structure — **92%**
2. Relevant experience and skills — **88%**
3. Natural keyword use (not stuffing) — **76%**
4. Short bullet points — **72%**
5. Simple formatting — **68%**
6. One-to-two page length — **64%**
7. Measurable achievements — **52%**

### 3.3 Quantification (mixed-tier, real effect)

- Lead with numbers. Recruiter consensus + the XYZ formula (§3.5) all converge here.
- **"Resumes with measurable results get 40% more callbacks"** and **"≥70% JD keyword alignment = 2.5× callbacks"** are widely cited but originate from **Jobscan (Tier 2 / vendor)** — directionally credible, **not peer-reviewed.** Treat as motivation, not as a hard number to promise.
- Caveat from recruiters: **<20%** of applicants include any specific achievement, so genuine, specific metrics are a strong differentiator.

### 3.4 Resume length — our one-page constraint is contestable (Tier 1)

**ResumeGo study** (482 recruiters/hiring managers/HR/C-suite in a hiring simulation; 7,712 resumes selected):

- Recruiters were **2.3× more likely** to prefer **two-page** resumes for candidates with **10+ years** experience.
- Even for **entry-level**, two-page was preferred **1.4×**.
- Two-page resumes scored **21% higher** on summarizing credentials.

**Important nuance / counter-signal:** This contradicts the dominant **tech/FAANG norm** (and most SWE recruiter advice) that strongly favors **one page** for engineers, especially early/mid-career. So: the bot's hard one-page constraint is *defensible for a SWE audience* but is **not universally optimal**, and the *mechanism* we use to enforce it (auto-shrinking font down to 0.8×) can hurt readability — which directly contradicts the Ladders "don't cram" finding (see §6).

### 3.5 The summary / "top third" matters most (Tier 1-ish)

- A 2025 **SHRM** hiring survey reportedly ranks a **clear, quantified summary as the single highest-value resume element** (cited secondhand — flagged).
- Results-based summaries that lead with numbers are cited as **3× more likely to pass initial screening** (vendor claim — flagged).
- This aligns with §1.4 (summary section is weight-heavy for ATS) **and** §3.1 (it's literally where the 7-second eye lands first). **High confidence that a present, keyword-dense, results-led summary helps both machine and human.**

### 3.6 The XYZ formula is sound and well-sourced (Tier 1)

"**Accomplished [X] as measured by [Y], by doing [Z]**" — popularized by **Laszlo Bock, former SVP People Operations at Google,** in *Work Rules!*. It forces an action verb, a metric, and a method into one line — exactly the three things a 7-second scan rewards. **Our bot already uses this — good.**

### 3.7 The factors the resume *can't* fix (important context)

Callbacks are also driven by levers no resume tweak addresses, and the research repeatedly points here:
- **Application timing / volume** — applying early in a high-volume window; applying to enough roles.
- **Referrals** — bypass the search-and-rank bottleneck entirely (the single highest-leverage channel).
- **Genuine qualification fit** vs the knockout gates (years, authorization, degree, location).
- **Title match** to the recruiter's Boolean search.

**A perfectly tailored resume is necessary but not sufficient.** If callback volume is low despite good resumes, part of the answer is likely *channel and volume*, not document quality.

---

## 4. Comparable products and the features they ship

### 4.1 Jobscan (market leader, the keyword-match benchmark)

- Core: paste resume + JD → **Match Rate %** + report. Checks **hard skills, soft skills, job-title match, education level, other keywords/buzzwords**, plus section presence and parse-risk formatting (tables/images/headers).
- **Recommends a 75–80% match rate** as the "sweet spot" — explicitly **warns against over-stuffing** ("don't copy-paste every keyword… it backfires with humans").
- **Feature we lack:** a concrete, surfaced **match-rate score against the specific JD** with a target band, plus a **gap list** of missing must-have keywords. This is their entire value proposition and it's the most copyable feature.

### 4.2 Teal

- All-in-one: resume builder + **AI keyword matching against a linked JD** + **application tracker** + interview prep. Generous free tier.
- **Feature we lack:** persistent **per-application tracking** and a workflow that ties resume version → job → outcome (useful for *measuring callback rate*, which is exactly our problem — we can't improve what we don't measure).

### 4.3 Rezi

- Deep specialization in **ATS-optimized generation**: AI bullet generation, keyword analysis, **real-time ATS score**, single-column templates that score **88%+ parse rate** across the four major engines.
- **Validated design choice we already share:** single-column template = high parse rate. Good — our PDF is single-column.

### 4.4 Resume Worded

- Strength: **recruiter-style line-by-line feedback** and **LinkedIn optimization**. Best-in-class for **writing/bullet quality** rather than raw keyword gaps.
- **Feature parallel:** this is essentially what our **Evaluator (critic) agent** does — line-level feedback. Our architecture is sound; the differentiation is in rubric quality.

### 4.5 Synthesis — where the good products converge

1. **Single-column, text-layer-clean output** (we do this).
2. **An explicit, surfaced match score vs the JD with a target band + gap list** (we *don't* — biggest gap).
3. **Keyword placement into summary + skills, in context, not stuffed** (we do this partially; summary is optional — a gap).
4. **Recruiter-style critique loop** (we do this — Evaluator agent).
5. **Application tracking to measure outcomes** (we don't — and it's how you'd actually diagnose a low-callback problem).

---

## 5. Fact-check of claims made earlier in this design conversation

The earlier conversation made specific claims about the template. Re-verified against research, given that **our output is a Puppeteer/Chrome-rendered text-layer PDF**, not raw HTML handed to the ATS:

| Earlier claim | Verdict | Notes |
|---|---|---|
| "CSS `::before` bullets will be invisible to ATS" | **Withdrawn / incorrect for our pipeline** ✔ correctly retracted | Chrome's print-to-PDF rasterizes CSS-generated content into the **text layer** as real glyphs; bullets extract fine. The retraction in the second message was right. |
| "Flexbox header (company left / date right) parses scrambled" | **Withdrawn / incorrect for our pipeline** ✔ correctly retracted | In a text PDF, extraction is by line (y-coordinate) left→right; "Company …… Date" on one line extracts cleanly. Scrambling is a **multi-column body** problem (§2.4), which we don't have. |
| "`div` vs `<h2>` section titles hurts ATS section segmentation" | **Withdrawn / incorrect for our pipeline** ✔ correctly retracted | Chrome's default PDF is **not tagged**, so there are no heading semantics either way. ATS segments by **text patterns** ("Experience" on its own line), which our template provides. |
| "LaTeX/Computer Modern font ligatures (fi, fl) can break ATS text extraction" | **True but moot for us** ✔ appropriately hedged | Real issue for Type-1 CM fonts lacking ToUnicode. But on the Linux render host those fonts are absent, so the stack falls back to Times New Roman / system serif, which embed proper ToUnicode. Keep the caveat; don't install CM on the server. |
| "Single-column clean template is ATS-safe" | **Confirmed** ✔ | Strongly supported: single-column ≈ 93% skills extraction vs ~46% multi-column. Our template is single-column — a genuine strength. |
| "Summary is the densest keyword zone and should be required" | **Confirmed** ✔ | §1.4, §3.5. Making it default-on is well-supported. |
| "No job-title mirroring is a gap; ATS runs a title filter first" | **Confirmed and *upgraded* in importance** ✔✔ | §2.3 — recruiter **Boolean search on title** is arguably *the* gating step. This is higher-impact than originally framed. |
| "Formatting auto-graded 10/10 in the evaluator gives away points" | **Confirmed as a rubric weakness** ✔ | The PDF *is* format-safe, so 10/10 is defensible — but the evaluator should still validate **content-level** parse risks (date-format consistency, over-long skill strings) rather than blind-granting. |

**Net:** The conversation's *initial* alarm about the HTML template was **wrong**, and the **self-correction was correct.** The **prompt-level** recommendations (keyword extraction, required summary, title mirroring) **survive fact-checking and are the right focus.**

---

## 6. Audit — what our current implementation does right and wrong

### 6.1 What we're doing RIGHT (keep / protect)

1. **Single-column, text-layer PDF via Puppeteer** — high parse reliability (§2.4, §4.3). Do not "fix" this.
2. **XYZ / quantification enforcement** in both generator and evaluator personas — Tier-1-backed (§3.6).
3. **Generator → Evaluator critic loop** — mirrors Resume Worded's value (§4.4); good architecture.
4. **Hallucination grounding** against the master profile — protects against the credibility risk that invented facts get exposed in interviews.
5. **Company-type personas** (startup / GCC / IT services) — a reasonable proxy for matching the *tone* recruiters in each segment skim for.
6. **Structured JSON → Python-assembled HTML** — keeps the model out of layout, preserving the safe template.

### 6.2 What we're doing WRONG or under-investing in (ranked by likely callback impact)

1. **No explicit JD keyword extraction + coverage scoring (highest impact).** We tailor "implicitly." Jobscan's entire product is the explicit version: extract must-have hard skills/title/keywords from the JD, then **measure coverage against a 75–80% target** and surface the **gap list**. We don't extract, don't measure, don't target. This is the single biggest miss and directly maps to §2.3 (recruiter search) and §1.5 (score bands).
2. **No job-title mirroring (highest impact, cheap fix).** §2.3 says title-based Boolean search is the gating step. The generator is never told to echo the JD's exact role title in the summary / most-recent role framing. Cheap, high-leverage.
3. **Summary is optional (`summary: str | None`).** It's the highest-weight zone for both ATS (§1.4) and the 7-second human scan (§3.1, §3.5). Should be **default-on and keyword-dense**, optional only as a last resort for space.
4. **One-page enforcement by font auto-shrink (down to 0.8×) risks "cramming."** Directly contradicts the Ladders "don't cram" finding (§3.1) and ResumeGo's length data (§3.4). At 0.8× of 10pt that's **8pt body text** — below comfortable print readability. Better to enforce one page by **cutting/condensing content** (which the generator controls) than by shrinking type. Consider relaxing to allow a clean two-page for senior JDs.
5. **Evaluator blind-grants formatting 10/10.** Should instead validate content-level parse hygiene: consistent date formats, no exotic Unicode leaking in, skill strings not absurdly long, contact info present in body.
6. **"Truthful extrapolation" invents metrics (e.g., "~40% reduction").** Quantification helps (§3.3), but **fabricated round numbers are a credibility risk** in interviews and can read as generic AI filler (which §1.3 says systems now flag). Prefer real numbers from the master profile; if extrapolating, keep it conservative and defensible, and consider flagging invented metrics for user confirmation.
7. **Keyword-stuffing risk from aggressive personas.** The startup persona's "AGGRESSIVELY penalize / demand high-ownership verbs" can push toward buzzword density. §1.3 and §3.2 (76% want *natural* keyword use) warn that stuffing now triggers red-flag detection and annoys humans. Balance ownership verbs with natural language.
8. **No outcome measurement.** We can't diagnose a low-callback problem without tracking application → resume version → callback. Teal/Jobscan both treat tracking as core (§4.2). We log analytics already (`analytics_logger`) — extending it to callback outcomes would let us actually close the loop.
9. **The resume is necessary-but-not-sufficient (§3.7).** No amount of prompt tuning fixes a channel/volume problem. Worth telling the user plainly: referrals and application volume/timing likely matter as much as the document. (The bot already has an email-finder / cold-outreach flow — that's the right instinct; lean into it.)

---

## 7. Prioritized recommendations

**Tier A — do first (highest callback leverage, low effort):**
1. Add an **explicit JD keyword-extraction step** to the generator: pull must-have hard skills, tools, and the exact **role title** verbatim *before* writing; anchor `skills[]` and `summary` to them.
2. Add **job-title mirroring** — echo the JD's exact title in the summary and frame the most-recent role to align (truthfully).
3. Make the **summary default-on** and keyword-dense (results-led, leads with a number where possible).
4. Add a **keyword-coverage score** to the Evaluator: compute % of JD must-have keywords present, target **75–80%**, and emit a **gap list** as feedback. This turns our critic into a Jobscan-style match-rate engine.

**Tier B — do next (quality / credibility):**
5. Replace blind 10/10 formatting score with **content-level parse-hygiene checks** (date consistency, Unicode, skill-string length, contact-in-body).
6. **Constrain metric fabrication** — prefer real numbers; mark extrapolated metrics for user confirmation; soften personas to avoid keyword-stuffing.
7. Reconsider **one-page enforcement**: cut content rather than shrink font below ~10pt; consider allowing two pages for senior/10+ yr JDs.

**Tier C — strategic (measurement & channel):**
8. Extend `analytics_logger` to track **callback outcomes per application**, so match-rate and persona choices can be correlated with real results.
9. Lean into the **referral / cold-outreach** flow as a first-class callback lever, not an afterthought — it bypasses the search-and-rank bottleneck that §2.3 identifies as the real wall.

---

## 8. Sources

**Tier 1 — authoritative / primary**
- Harvard Business School, *Hidden Workers: Untapped Talent* (2021), Fuller, Raman, Sage-Gavin, Hines — https://www.hbs.edu/managing-the-future-of-work/Documents/research/hiddenworkers09032021.pdf
- The Ladders, *Eye-Tracking Study* (2018 update) — https://www.theladders.com/static/images/basicSite/pdfs/TheLadders-EyeTracking-StudyC2.pdf ; coverage: https://www.hrdive.com/news/eye-tracking-study-shows-recruiters-look-at-resumes-for-7-seconds/541582/ ; https://www.prnewswire.com/news-releases/ladders-updates-popular-recruiter-eye-tracking-study-with-new-key-insights-on-how-job-seekers-can-improve-their-resumes-300744217.html
- ResumeGo, *One or Two Page Resumes* study (482 professionals) — https://www.resumego.net/research/one-or-two-page-resumes/ ; coverage: https://www.ere.net/articles/one-or-two-page-resumes-best
- Enhancv, *Does the ATS Reject Your Resume? 25 Recruiters Explain* (25-recruiter survey) — https://enhancv.com/blog/does-ats-reject-resumes/
- Laszlo Bock (ex-Google SVP People Ops), *Work Rules!* — XYZ formula; overview: https://www.tealhq.com/post/xyz-resume

**Tier 1/2 — myth fact-check & synthesis**
- The Interview Guys, *The ATS Resume Rejection Myth* (Preptel origin; Assaf investigation) — https://blog.theinterviewguys.com/ats-resume-rejection-myth/
- Hiration, *No, an ATS Isn't Auto-Rejecting Your Resume* — https://www.hiration.com/blog/ats-auto-reject-resume-myth/
- HiringThing, *Applicant Tracking Systems Aren't Excluding Applicants—People Are* — https://blog.hiringthing.com/applicant-tracking-system-myths

**Tier 2 — vendor / product (used for product features and flagged statistics)**
- Jobscan, *What Match Rate Should I Aim For?* — https://www.jobscan.co/blog/what-jobscan-match-rate-should-i-aim-for/
- Jobscan, *Resume Tables & Columns Break Parsing* — https://www.jobscan.co/blog/resume-tables-columns-ats/
- Jobscan, *ATS Formatting Mistakes* — https://www.jobscan.co/blog/ats-formatting-mistakes/
- Teal, *Best AI Resume Builders* / *Quantify Your Resume* — https://www.tealhq.com/post/best-ai-resume-builders ; https://www.tealhq.com/post/quantify-your-resume
- Rezi, *Best AI Resume Builders* — https://www.rezi.ai/posts/best-ai-resume-builders
- Resume Worded, *Jobscan vs Resume Worded* — https://resumeworded.com/blog/jobscan-vs-resume-worded/

**Tier 2/3 — corroborating consensus on parsing & format (PDF vs DOCX, single vs multi-column)**
- Resumemate, *PDF vs Word 2026* — https://www.resumemate.io/blog/pdf-vs-word-for-resume-2026-which-format-ats-actually-prefers/
- Scale.jobs, *Top 5 ATS Resume Scanners Accuracy Test* — https://scale.jobs/blog/ats-resume-scanners-comparison-accuracy-test
- The Interview Guys, *What Semantic Matching Means* — https://blog.theinterviewguys.com/what-semantic-matching-means/

**Academic (for further reading; not load-bearing here)**
- *The Algorithmic Barrier: Quantifying Artificial Frictional Unemployment in Automated Recruitment Systems* — https://arxiv.org/pdf/2601.14534
- *Better Together: Quantifying the Benefits of AI-Assisted Recruitment* — https://arxiv.org/pdf/2507.08029
