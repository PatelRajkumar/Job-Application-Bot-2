"""
ATS Parseability Test Script for Resume PDFs
Tests that Puppeteer-generated PDFs are fully readable by ATS systems.
"""

import sys
import re
import argparse
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

import pdfplumber
import pypdf
import fitz  # PyMuPDF


PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
WARN = "\033[33mWARN\033[0m"
INFO = "\033[36mINFO\033[0m"


@dataclass
class CheckResult:
    name: str
    status: str  # PASS / FAIL / WARN
    message: str
    detail: Optional[str] = None


@dataclass
class TestReport:
    pdf_path: str
    results: list[CheckResult] = field(default_factory=list)

    def add(self, name: str, status: str, message: str, detail: str = None):
        self.results.append(CheckResult(name, status, message, detail))

    def print(self):
        print(f"\n{'='*70}")
        print(f"ATS Parseability Report: {self.pdf_path}")
        print(f"{'='*70}")
        for r in self.results:
            label = PASS if r.status == "PASS" else (FAIL if r.status == "FAIL" else (INFO if r.status == "INFO" else WARN))
            print(f"[{label}] {r.name}: {r.message}")
            if r.detail:
                for line in r.detail.strip().split("\n"):
                    print(f"       {line}")

        passes = sum(1 for r in self.results if r.status == "PASS")
        fails = sum(1 for r in self.results if r.status == "FAIL")
        warns = sum(1 for r in self.results if r.status == "WARN")
        print(f"\n{'='*70}")
        print(f"Summary: {passes} passed, {warns} warnings, {fails} failed")
        print(f"{'='*70}\n")
        return fails == 0


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def check_not_encrypted(report: TestReport, pdf_path: str):
    reader = pypdf.PdfReader(pdf_path)
    if reader.is_encrypted:
        report.add("Encryption", "FAIL",
                   "PDF is password-protected — ATS cannot open it")
    else:
        report.add("Encryption", "PASS", "PDF is not encrypted")


def check_page_count(report: TestReport, pdf_path: str):
    with pdfplumber.open(pdf_path) as pdf:
        n = len(pdf.pages)
    if n == 1:
        report.add("Page Count", "PASS", f"Single page ({n})")
    else:
        report.add("Page Count", "WARN",
                   f"{n} pages — ATS parsers vary in multi-page support; single-page is safest")


def check_text_extractable(report: TestReport, pdf_path: str) -> str:
    """Returns the full extracted text for downstream checks."""
    with pdfplumber.open(pdf_path) as pdf:
        pages_text = []
        for page in pdf.pages:
            t = page.extract_text(x_tolerance=3, y_tolerance=3)
            pages_text.append(t or "")
        full_text = "\n".join(pages_text)

    word_count = len(full_text.split())
    if word_count < 50:
        report.add("Text Extraction", "FAIL",
                   f"Only {word_count} words extracted — PDF may be image-based",
                   f"Extracted text preview:\n{full_text[:300]!r}")
    elif word_count < 150:
        report.add("Text Extraction", "WARN",
                   f"Only {word_count} words extracted — suspiciously low for a resume")
    else:
        report.add("Text Extraction", "PASS",
                   f"{word_count} words extracted successfully")
    return full_text


def check_no_image_only_pages(report: TestReport, pdf_path: str):
    """Detect pages where all content is rasterized (image) rather than text vectors."""
    doc = fitz.open(pdf_path)
    for i, page in enumerate(doc):
        text = page.get_text("text").strip()
        images = page.get_images(full=True)
        if not text and images:
            report.add("Image Pages", "FAIL",
                       f"Page {i+1} has no text layer — content is rasterized images only")
            return
        # Also flag pages that are mostly images with very little text
        if images and len(text) < 100:
            report.add("Image Pages", "WARN",
                       f"Page {i+1} has {len(images)} image(s) and minimal text ({len(text)} chars) — verify no text is rasterized")
            return
    report.add("Image Pages", "PASS", "No image-only pages detected")
    doc.close()


def check_font_embedding(report: TestReport, pdf_path: str):
    doc = fitz.open(pdf_path)
    all_fonts = []
    not_embedded = []
    for page in doc:
        for font in page.get_fonts(full=True):
            # font tuple: (xref, ext, type, basefont, name, encoding, referencer)
            name = font[3] or font[4]
            embedded = font[1] != "n/a"  # ext field; "n/a" means not embedded
            all_fonts.append((name, font[2], embedded))
            if not embedded:
                not_embedded.append(name)
    doc.close()

    unique_fonts = list({f[0] for f in all_fonts})
    if not_embedded:
        report.add("Font Embedding", "WARN",
                   f"{len(not_embedded)} font(s) not embedded — ATS may substitute and misread characters",
                   f"Not embedded: {', '.join(set(not_embedded))}\nAll fonts: {', '.join(unique_fonts)}")
    else:
        report.add("Font Embedding", "PASS",
                   f"All {len(unique_fonts)} font(s) embedded: {', '.join(unique_fonts)}")


def check_character_encoding(report: TestReport, text: str):
    """Detect replacement characters and common encoding corruption patterns."""
    replacement_char_count = text.count("�")
    # Patterns like Ã© (UTF-8 bytes interpreted as Latin-1)
    latin1_corruption = len(re.findall(r'[ÃÂ]\S', text))
    # Null bytes or control characters (except newline/tab)
    control_chars = len(re.findall(r'[\x00-\x08\x0b-\x1f]', text))

    issues = []
    if replacement_char_count:
        issues.append(f"{replacement_char_count} replacement char(s) (U+FFFD)")
    if latin1_corruption:
        issues.append(f"{latin1_corruption} possible Latin-1/UTF-8 mismatch pattern(s)")
    if control_chars:
        issues.append(f"{control_chars} unexpected control character(s)")

    if issues:
        report.add("Character Encoding", "FAIL",
                   "Encoding issues found: " + "; ".join(issues))
    else:
        report.add("Character Encoding", "PASS", "No encoding corruption detected")


def check_reading_order(report: TestReport, pdf_path: str):
    """
    Checks that text flows roughly top-to-bottom, left-to-right.
    Flex-layout date spans on the right can land out-of-order in the text stream —
    ATS may read them mid-sentence, corrupting job titles and company names.
    """
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        words = page.extract_words(x_tolerance=3, y_tolerance=3, keep_blank_chars=False)

    if not words:
        report.add("Reading Order", "WARN", "No word objects found, cannot verify order")
        return

    # Group words into lines (within 5pt vertical tolerance)
    lines: list[list[dict]] = []
    current_line = [words[0]]
    for w in words[1:]:
        if abs(w["top"] - current_line[0]["top"]) <= 5:
            current_line.append(w)
        else:
            lines.append(sorted(current_line, key=lambda x: x["x0"]))
            current_line = [w]
    lines.append(sorted(current_line, key=lambda x: x["x0"]))

    # Verify lines are sorted top-to-bottom
    line_tops = [line[0]["top"] for line in lines]
    out_of_order = sum(1 for i in range(1, len(line_tops)) if line_tops[i] < line_tops[i-1] - 5)

    if out_of_order:
        report.add("Reading Order", "WARN",
                   f"{out_of_order} line(s) appear out of vertical order — ATS may scramble content",
                   "This can happen with flex-layout date spans placed to the right of job titles")
    else:
        report.add("Reading Order", "PASS",
                   f"Text flows top-to-bottom across {len(lines)} detected lines")


def check_selectable_text_coverage(report: TestReport, pdf_path: str, text: str):
    """
    Uses PyMuPDF's raw dict extraction to verify the character count matches
    what pdfplumber sees — large discrepancies suggest hidden or non-selectable chars.
    """
    doc = fitz.open(pdf_path)
    raw_text = "".join(page.get_text("text") for page in doc)
    doc.close()

    ratio = len(raw_text.strip()) / max(len(text.strip()), 1)
    if ratio < 0.85:
        report.add("Text Coverage", "WARN",
                   f"PyMuPDF extracts {len(raw_text.strip())} chars vs pdfplumber's {len(text.strip())} ({ratio:.0%} match) — some text may be non-selectable")
    else:
        report.add("Text Coverage", "PASS",
                   f"Text coverage consistent across parsers ({len(text.strip())} chars, {ratio:.0%} match)")


def check_resume_sections(report: TestReport, text: str):
    """Verify standard resume sections are present and readable."""
    text_lower = text.lower()
    sections = {
        "Contact Info": [r'\d{10}', r'@\w+\.\w+', r'linkedin', r'github'],
        "Experience": [r'experience', r'work history', r'employment'],
        "Skills": [r'skills', r'technologies', r'proficiencies'],
        "Education": [r'education', r'university', r'degree', r'bachelor', r'master'],
    }
    missing = []
    found = []
    for section, patterns in sections.items():
        if any(re.search(p, text_lower) for p in patterns):
            found.append(section)
        else:
            missing.append(section)

    if missing:
        report.add("Resume Sections", "WARN",
                   f"Could not detect: {', '.join(missing)}",
                   f"Detected: {', '.join(found)}")
    else:
        report.add("Resume Sections", "PASS",
                   f"All key sections detected: {', '.join(found)}")


def check_css_generated_bullets(report: TestReport, text: str):
    """
    CSS ::before bullets (content: '•') are often NOT included in the PDF text
    layer by Chromium. Check whether bullets appear in extracted text.
    """
    bullet_chars = ["•", "·", "‣", "▸", "▪", "-"]
    found = [c for c in bullet_chars if c in text]
    # Count lines that start with whitespace + text (would be a bullet item without bullet)
    # as a proxy for list items
    list_lines = [l for l in text.split("\n") if re.match(r'^\s{2,}\S', l)]

    if not found and list_lines:
        report.add("CSS Bullets", "WARN",
                   "No bullet characters found in extracted text — CSS ::before bullets may be missing from text layer",
                   "ATS parsers may misparse list items as run-on text. Consider using actual Unicode bullets in HTML (•) instead of CSS content property.")
    elif found:
        report.add("CSS Bullets", "PASS",
                   f"Bullet character(s) present in text layer: {' '.join(found)}")
    else:
        report.add("CSS Bullets", "PASS", "No list items detected (nothing to check)")


def check_flex_layout_dates(report: TestReport, pdf_path: str):
    """
    Flex-layout (justify-content: space-between) places dates far right.
    In Chrome PDFs this often breaks the text stream: dates can appear after
    the next section header instead of on the same line as the job title.
    We detect this by checking if date-like tokens appear far from the left margin.
    """
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        words = page.extract_words(x_tolerance=3, y_tolerance=3)

    if not words:
        report.add("Flex-Layout Dates", "WARN", "No words found")
        return

    page_width = page.width
    date_pattern = re.compile(
        r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|January|February|March|April|June|July|August|September|October|November|December)\b'
        r'|\b(20\d{2}|19\d{2})\b'
        r'|\bPresent\b', re.IGNORECASE
    )

    date_words = [w for w in words if date_pattern.search(w["text"])]
    right_side_dates = [w for w in date_words if w["x0"] > page_width * 0.55]

    # Now verify that for each right-side date, there's a word on the same line to the left
    orphaned = []
    for dw in right_side_dates:
        same_line = [w for w in words
                     if abs(w["top"] - dw["top"]) <= 5 and w["x0"] < dw["x0"] - 20]
        if not same_line:
            orphaned.append(dw["text"])

    if orphaned:
        report.add("Flex-Layout Dates", "WARN",
                   f"{len(orphaned)} date token(s) appear right-aligned with no left-side text on same line",
                   f"Tokens: {orphaned}\nThis may indicate dates are in a separate text stream from job titles.")
    else:
        report.add("Flex-Layout Dates", "PASS",
                   f"Found {len(right_side_dates)} date token(s) — all co-exist with left-side content on the same line")


def check_pdf_metadata(report: TestReport, pdf_path: str):
    reader = pypdf.PdfReader(pdf_path)
    meta = reader.metadata or {}
    issues = []
    if not meta.get("/Title"):
        issues.append("no /Title")
    if not meta.get("/Author"):
        issues.append("no /Author")

    if issues:
        report.add("PDF Metadata", "WARN",
                   f"Missing metadata: {', '.join(issues)} — some ATS use metadata to index documents",
                   f"Current metadata: {dict(meta)}")
    else:
        report.add("PDF Metadata", "PASS",
                   f"Title: {meta.get('/Title')!r}, Author: {meta.get('/Author')!r}")


def check_hyperlinks_readable(report: TestReport, text: str):
    """Verify that hyperlink text (email, LinkedIn, GitHub) is in the text layer."""
    patterns = {
        "Email": r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}',
        "LinkedIn": r'linkedin\.com',
        "GitHub": r'github\.com',
    }
    missing = []
    found = []
    for label, pattern in patterns.items():
        if re.search(pattern, text, re.IGNORECASE):
            found.append(label)
        else:
            missing.append(label)

    if missing:
        report.add("Hyperlinks in Text", "WARN",
                   f"Not found in text layer: {', '.join(missing)}",
                   f"Found: {', '.join(found)}")
    else:
        report.add("Hyperlinks in Text", "PASS",
                   f"All contact links present in text layer: {', '.join(found)}")


def check_special_characters(report: TestReport, text: str):
    """Check that common resume special chars (em-dash, en-dash, bullets) survived encoding."""
    checks = {
        "En-dash (–)": "–",
        "Em-dash (—)": "—",
    }
    for label, char in checks.items():
        if char in text:
            report.add(f"Special Char: {label}", "PASS", f"'{char}' present in text layer")
        # Not a failure — just informational; these may legitimately be absent


def check_small_caps_name_split(report: TestReport, text: str):
    """
    CSS font-variant: small-caps causes Chrome to split words into separate text
    objects (one per glyph size change), so "PURVESH GANDHI" becomes "P G\nURVESH ANDHI".
    Detect this by looking for suspiciously short capitalized tokens at the top of the doc.
    """
    first_lines = [l.strip() for l in text.split("\n")[:5] if l.strip()]
    # A properly extracted name would be 2+ chars; a split name shows 1-2 char tokens
    short_caps_tokens = [l for l in first_lines if re.match(r'^[A-Z ]{1,3}$', l)]
    if short_caps_tokens:
        report.add("Small-Caps Name Split", "FAIL",
                   "Name appears split into single-letter tokens — font-variant:small-caps bug",
                   f"Top lines of extracted text: {first_lines}\n"
                   "Fix: remove font-variant:small-caps from .name and .section-title, or switch to a font that natively supports small-caps glyphs.")
    else:
        report.add("Small-Caps Name Split", "PASS",
                   f"Name/header lines look intact: {first_lines[:3]}")


def show_extracted_text_preview(report: TestReport, text: str):
    """Always show first 800 chars of extracted text so user can spot-check."""
    preview = text[:800].strip()
    report.add("Extracted Text Preview", "INFO",
               "First 800 characters of extracted text:",
               preview)


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_checks(pdf_path: str, verbose: bool = False) -> bool:
    path = Path(pdf_path)
    if not path.exists():
        print(f"Error: file not found: {pdf_path}")
        return False

    report = TestReport(pdf_path=str(path))

    print(f"\nRunning ATS parseability checks on: {path.name}")
    print("-" * 70)

    # Run all checks
    check_not_encrypted(report, pdf_path)
    check_page_count(report, pdf_path)
    text = check_text_extractable(report, pdf_path)
    check_no_image_only_pages(report, pdf_path)
    check_font_embedding(report, pdf_path)
    check_character_encoding(report, text)
    check_reading_order(report, pdf_path)
    check_selectable_text_coverage(report, pdf_path, text)
    check_resume_sections(report, text)
    check_css_generated_bullets(report, text)
    check_flex_layout_dates(report, pdf_path)
    check_pdf_metadata(report, pdf_path)
    check_hyperlinks_readable(report, text)
    check_special_characters(report, text)
    check_small_caps_name_split(report, text)

    if verbose:
        show_extracted_text_preview(report, text)

    return report.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test resume PDF ATS parseability")
    parser.add_argument("pdf", nargs="?",
                        default="samples/PurveshGandhi_Resume_Zoom.pdf",
                        help="Path to PDF file(s) to test")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show extracted text preview")
    parser.add_argument("pdfs", nargs="*", help="Additional PDF files to test")
    args = parser.parse_args()

    all_files = [args.pdf] + (args.pdfs or [])
    all_passed = True
    for pdf_file in all_files:
        passed = run_checks(pdf_file, verbose=args.verbose)
        all_passed = all_passed and passed

    sys.exit(0 if all_passed else 1)
