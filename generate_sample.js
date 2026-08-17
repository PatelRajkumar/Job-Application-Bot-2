const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');

const profile = JSON.parse(fs.readFileSync(path.join(__dirname, 'master_profile.json'), 'utf8'));
const template = fs.readFileSync(path.join(__dirname, 'resume_template.html'), 'utf8');

function e(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

// Headline
const headlineHtml = `<div class="headline">Software Engineer &mdash; Backend &amp; Full-Stack</div>`;

// Skills
const s = profile.skills;
const skillRows = [
    { category: 'Languages', items: s.languages },
    { category: 'Frameworks', items: s.frameworks },
    { category: 'Databases', items: s.databases },
    { category: 'Cloud', items: s.cloud },
    { category: 'Messaging', items: s.messaging },
    { category: 'Tools & Practices', items: s.tools_and_practices },
];
const skillsHtml = skillRows
    .map(r => `<li><strong>${e(r.category)}:</strong> ${r.items.map(e).join(', ')}</li>`)
    .join('\n            ');

// Experience
const exp = profile.experience[0];
const expBullets = exp.projects[0].impact_bullets.slice(0, 4)
    .concat(exp.projects[1].impact_bullets.slice(0, 3));
const expHtml = `<li class="subheading-item">
                <div class="subheading-row row-1">
                    <span class="left">${e(exp.company)}</span>
                    <span class="right">${e(exp.startDate)} &ndash; ${e(exp.endDate)}</span>
                </div>
                <div class="subheading-row row-2">
                    <span class="role">${e(exp.role)}</span>
                    <span class="location">${e(exp.location)}</span>
                </div>
                <ul class="item-list">
                    ${expBullets.map(b => `<li>${e(b)}</li>`).join('\n                    ')}
                </ul>
            </li>`;

// Projects
const proj = profile.side_projects[0];
const projHtml = `<li class="subheading-item">
                <div class="project-heading">
                    <span class="project-title"><strong>${e(proj.name)}</strong></span>
                    <span class="project-date">2025 &ndash; Present</span>
                </div>
                <div class="project-tech"><em>${proj.tech_stack.map(e).join(', ')}</em></div>
                <ul class="item-list">
                    ${proj.impact_bullets.slice(0, 3).map(b => `<li>${e(b)}</li>`).join('\n                    ')}
                </ul>
            </li>`;

// Education
const edu = profile.education[0];
const educationHtml = `<li class="subheading-item">
                <div class="subheading-row row-1">
                    <span class="left">${e(edu.institution)}</span>
                    <span class="right">${e(edu.location)}</span>
                </div>
                <div class="subheading-row row-2">
                    <span class="role">${e(edu.degree)}</span>
                    <span class="location"></span>
                </div>
            </li>`;

let html = template
    .replace('{{ HEADLINE_PLACEHOLDER }}', headlineHtml)
    .replace('{{ SUMMARY_PLACEHOLDER }}', '')
    .replace('{{ SKILLS_PLACEHOLDER }}', skillsHtml)
    .replace('{{ EXPERIENCE_PLACEHOLDER }}', expHtml)
    .replace('{{ PROJECTS_PLACEHOLDER }}', projHtml)
    .replace('{{ EDUCATION_PLACEHOLDER }}', educationHtml);

(async () => {
    const browser = await puppeteer.launch({ headless: true });
    const page = await browser.newPage();
    await page.setContent(html, { waitUntil: 'networkidle0' });
    const outPath = path.join(__dirname, `sample_resume_${Date.now()}.pdf`);
    await page.pdf({
        path: outPath,
        format: 'Letter',
        printBackground: true,
        margin: { top: 0, bottom: 0, left: 0, right: 0 },
        displayHeaderFooter: false,
    });

    // Inject PDF metadata (/Title and /Author) so ATS parsers can index the doc
    const { PDFDocument } = require('pdf-lib');
    const bytes = require('fs').readFileSync(outPath);
    const doc = await PDFDocument.load(bytes);
    doc.setTitle('Purvesh Gandhi - Resume');
    doc.setAuthor('Purvesh Gandhi');
    doc.setSubject('Software Engineer Resume');
    doc.setKeywords(['Software Engineer', 'Backend', 'Full-Stack', 'Node.js']);
    require('fs').writeFileSync(outPath, await doc.save());

    await browser.close();
    console.log(`PDF saved to ${outPath}`);
})();
