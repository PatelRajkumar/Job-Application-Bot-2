from models import TailoredResumeContent, SkillRow, ExperienceRole, Project
from html_builder import build_resume_html, build_education_html
import json

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

master_profile_json = json.dumps({
    "education": [{"institution": "Ganpat University", "location": "Kherva, Gujarat", "degree": "B.Tech in Computer Engineering"}]
})

with open("../resume_template.html") as f:
    template = f.read()

education_html = build_education_html(master_profile_json)
html = build_resume_html(template, content, education_html)

assert "{{ SUMMARY_PLACEHOLDER }}" not in html
assert "{{ SKILLS_PLACEHOLDER }}" not in html
assert "{{ EXPERIENCE_PLACEHOLDER }}" not in html
assert "{{ PROJECTS_PLACEHOLDER }}" not in html
assert "{{ EDUCATION_PLACEHOLDER }}" not in html
assert "A brief summary." in html
assert "Wishtree Technologies" in html
assert "<strong>scalable systems</strong>" in html
assert "Ganpat University" in html
print("All assertions passed.")
