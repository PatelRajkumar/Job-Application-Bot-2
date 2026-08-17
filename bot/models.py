from pydantic import BaseModel, Field
from typing import List

class ResumeEvaluation(BaseModel):
    passed: bool = Field(description="True if resume meets the bar")
    is_hallucinated: bool = Field(description="True if fabricated facts exist")
    feedback: List[str] = Field(description="2–5 CRITICAL items that must be fixed before passing. Each uses a [VERB], [METRIC], [HALLUCINATION], [KEYWORD], or [CONTEXT] tag.")
    advisory: List[str] = Field(default_factory=list, description="0–3 ADVISORY improvement suggestions that do not block passing. Each uses an [ADVISORY] tag. Empty list if none apply.")
    ats_score: int = Field(description="Score for ATS parsing and keyword optimization (0-100)")
    manual_score: int = Field(description="Score for human recruiter impact and readability (0-100)")
    keyword_coverage: int = Field(default=0, description="Percentage (0–100) of must-have JD keywords present in the resume (semantic match allowed).")
    missing_keywords: List[str] = Field(default_factory=list, description="Must-have keywords from the JD that are absent from the resume.")

class ResumeEdit(BaseModel):
    search_string: str = Field(description="The exact contiguous text block in the current HTML to be replaced. Must be unique and match the existing HTML exactly including whitespace.")
    replacement_string: str = Field(description="The new text/HTML to replace it with.")

class ResumeRevisions(BaseModel):
    edits: List[ResumeEdit] = Field(description="List of targeted edits to apply to the HTML.")

class JDKeywords(BaseModel):
    target_title: str = Field(description="The exact role title from the JD, verbatim (e.g. 'Senior Backend Engineer'). Empty string if none stated.")
    must_have_keywords: list[str] = Field(description="5–12 must-have hard skills, tools, and technologies named or strongly implied as required in the JD. Exact surface forms.")
    nice_to_have_keywords: list[str] = Field(description="0–8 secondary/preferred keywords.")

class CompanySnippet(BaseModel):
    company_type: str = Field(description="The company type: 'product_startup', 'it_services', or 'gcc'.")
    target_title: str = Field(default="", description="The exact role title from the JD, verbatim (e.g. 'Senior Backend Engineer'). Empty string if none stated.")
    must_have_keywords: list[str] = Field(default_factory=list, description="5–12 must-have hard skills, tools, and technologies named or strongly implied as required in the JD. Exact surface forms.")
    nice_to_have_keywords: list[str] = Field(default_factory=list, description="0–8 secondary/preferred keywords.")

# ── Structured generation models ────────────────────────────────────────────

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
    headline: str | None = Field(default=None, description="Positioning headline under the name stating the TARGET role (e.g. 'Software Engineer — targeting Senior Backend / Full-Stack roles'). Echoes the JD target title. NOT a claim about employer records. Plain text only.")
    summary: str | None = Field(default=None, description="Optional 2–3 sentence professional summary. Plain text only — no HTML tags.")
    skills: list[SkillRow] = Field(description="5–7 skill rows, ordered by relevance to the JD")
    experience: list[ExperienceRole] = Field(description="Work experience entries, most recent first. Only include roles present in the master profile.")
    projects: list[Project] = Field(description="1–2 most relevant projects for this JD. Only include projects present in the master profile.")

# Note: EducationEntry is intentionally omitted from TailoredResumeContent.
# Education never changes between tailored resumes — it is hardcoded in build_resume_html()
# from master_profile_json rather than sent to the model.
