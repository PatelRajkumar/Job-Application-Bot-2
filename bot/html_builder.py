"""
bot/html_builder.py — Phase 2 of structured generation plan.

Pure Python HTML assembler.  No LLM dependencies, no imports from other bot
modules.  Takes a TailoredResumeContent object and the raw template string,
returns the completed HTML string.

Escaping strategy
-----------------
- _escape()  is applied to ALL plain-text fields: company names, dates, roles,
  locations, skill names, institution names, degrees, summary text.
- Bullet strings (bullets: list[str]) are NOT escaped — they are model-generated
  and may contain <strong> and <em> tags intentionally.  The generator prompt
  rules constrain the model to only those two tags.  If you later want to
  sanitise bullet HTML, add a bleach.clean() call on each bullet before the
  f'<li>{b}</li>' line.
"""

from __future__ import annotations

import html as _html
import json
import logging

from models import TailoredResumeContent

logger = logging.getLogger(__name__)

_ASCII_REPLACEMENTS = {
    '‘': "'",   # left single quote
    '’': "'",   # right single quote
    '“': '"',   # left double quote
    '”': '"',   # right double quote
    '–': '-',   # en dash
    '—': '-',   # em dash
    '…': '...',  # ellipsis
}

_SKILL_ITEM_MAX_LEN = 50


def _normalize_ascii(text: str) -> str:
    """Replace typographic Unicode characters with ASCII equivalents."""
    for char, replacement in _ASCII_REPLACEMENTS.items():
        text = text.replace(char, replacement)
    return text


def _escape(text: str) -> str:
    """Normalize typographic chars then HTML-escape plain-text fields."""
    return _html.escape(_normalize_ascii(text))


def build_education_html(master_profile_json: str) -> str:
    """
    Builds the {{ EDUCATION_PLACEHOLDER }} HTML from master_profile_json.

    Called once per session — education never changes between tailored resumes.

    Expected master_profile shape:
        { "education": [{ "institution": str, "location": str, "degree": str }] }
    """
    profile = json.loads(master_profile_json)
    edu_entries = profile.get("education", [])
    items: list[str] = []
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


def build_resume_html(
    template_html: str,
    content: TailoredResumeContent,
    education_html: str,
) -> str:
    """
    Substitutes TailoredResumeContent slot values into the resume template.
    Returns the completed HTML string.

    Parameters
    ----------
    template_html   : raw template string (loaded once; never sent to the model)
    content         : structured resume content produced by the generator
    education_html  : pre-built education block from build_education_html();
                      computed once per session, not per iteration
    """

    # -- Headline -------------------------------------------------------------
    if content.headline:
        headline_html = f'<div class="headline">{_escape(content.headline)}</div>'
    else:
        headline_html = ''

    # -- Summary --------------------------------------------------------------
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

    # -- Skills ---------------------------------------------------------------
    skills_lines: list[str] = []
    for row in content.skills:
        for item in row.items:
            if len(item) > _SKILL_ITEM_MAX_LEN:
                logger.warning(
                    "skills[%s].items: entry too long (%d chars) — may be a keyword dump: %r",
                    row.category, len(item), item[:60],
                )
        items_str = ', '.join(_escape(i) for i in row.items)
        skills_lines.append(
            f'<li><strong>{_escape(row.category)}:</strong> {items_str}</li>'
        )
    skills_html = '\n            '.join(skills_lines)

    # -- Experience -----------------------------------------------------------
    exp_items: list[str] = []
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

    # -- Projects -------------------------------------------------------------
    proj_items: list[str] = []
    for proj in content.projects:
        tech_str = ', '.join(_escape(t) for t in proj.tech_stack)
        bullets_html = '\n                    '.join(
            f'<li>{b}</li>' for b in proj.bullets  # bullets may contain <strong>/<em>
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

    # -- Substitute placeholders ----------------------------------------------
    # education_html is passed in pre-built — computed once from master_profile_json
    # via build_education_html(), never regenerated by the model.
    html = template_html
    html = html.replace('{{ HEADLINE_PLACEHOLDER }}', headline_html)
    html = html.replace('{{ SUMMARY_PLACEHOLDER }}', summary_html)
    html = html.replace('{{ SKILLS_PLACEHOLDER }}', skills_html)
    html = html.replace('{{ EXPERIENCE_PLACEHOLDER }}', experience_html)
    html = html.replace('{{ PROJECTS_PLACEHOLDER }}', projects_html)
    html = html.replace('{{ EDUCATION_PLACEHOLDER }}', education_html)

    return html
