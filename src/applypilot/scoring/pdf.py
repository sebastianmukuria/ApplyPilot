"""Text-to-PDF conversion for tailored resumes and cover letters.

Parses the structured text resume format, renders via an HTML/CSS template,
and exports to PDF using headless Chromium via Playwright.
"""

import logging
import re
from pathlib import Path

from applypilot.config import TAILORED_DIR

log = logging.getLogger(__name__)


# ── Resume Parser ────────────────────────────────────────────────────────

def parse_resume(text: str) -> dict:
    """Parse a structured text resume into sections.

    Expects a format with header lines (name, title, location, contact)
    followed by ALL-CAPS section headers (SUMMARY, TECHNICAL SKILLS, etc.).

    Args:
        text: Full resume text.

    Returns:
        {"name": str, "title": str, "location": str, "contact": str, "sections": dict}
    """
    lines = [line.rstrip() for line in text.strip().split("\n")]

    # Header: first few lines before SUMMARY
    header_lines: list[str] = []
    body_start = 0
    for i, line in enumerate(lines):
        if line.strip().upper() == "SUMMARY":
            body_start = i
            break
        if line.strip():
            header_lines.append(line.strip())

    name = header_lines[0] if len(header_lines) > 0 else ""
    title = header_lines[1] if len(header_lines) > 1 else ""
    # The header may have 3 or 4 lines depending on whether location is included
    location = ""
    contact = ""
    if len(header_lines) > 3:
        location = header_lines[2]
        contact = header_lines[3]
    elif len(header_lines) > 2:
        # Could be location or contact -- check for email/phone indicators
        if "@" in header_lines[2] or "|" in header_lines[2]:
            contact = header_lines[2]
        else:
            location = header_lines[2]

    # Split body into sections by ALL-CAPS headers
    sections: dict[str, str] = {}
    current_section: str | None = None
    current_lines: list[str] = []

    for line in lines[body_start:]:
        stripped = line.strip()
        # Detect section headers (all caps, no leading dash/bullet, longer than 3 chars)
        if (
            stripped
            and stripped == stripped.upper()
            and not stripped.startswith("-")
            and len(stripped) > 3
            and not stripped.startswith("\u2022")
        ):
            if current_section:
                sections[current_section] = "\n".join(current_lines).strip()
            current_section = stripped
            current_lines = []
        else:
            current_lines.append(line)

    if current_section:
        sections[current_section] = "\n".join(current_lines).strip()

    return {
        "name": name,
        "title": title,
        "location": location,
        "contact": contact,
        "sections": sections,
    }


def parse_skills(text: str) -> list[tuple[str, str]]:
    """Parse skills section into (category, value) pairs.

    Args:
        text: The TECHNICAL SKILLS section text.

    Returns:
        List of (category_name, skills_string) tuples.
    """
    skills: list[tuple[str, str]] = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if ":" in line:
            cat, val = line.split(":", 1)
            skills.append((cat.strip(), val.strip()))
    return skills


def parse_entries(text: str) -> list[dict]:
    """Parse experience/project entries from section text.

    Args:
        text: The EXPERIENCE or PROJECTS section text.

    Returns:
        List of {"title": str, "subtitle": str, "bullets": list[str]} dicts.
    """
    entries: list[dict] = []
    lines = text.strip().split("\n")
    current: dict | None = None

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- ") or stripped.startswith("\u2022 "):
            if current:
                current["bullets"].append(stripped[2:].strip())
        elif current is None or (
            not stripped.startswith("-")
            and not stripped.startswith("\u2022")
            and len(current.get("bullets", [])) > 0
        ):
            # New entry
            if current:
                entries.append(current)
            current = {"title": stripped, "subtitle": "", "bullets": []}
        elif current and not current["subtitle"]:
            current["subtitle"] = stripped
        else:
            if current:
                current["bullets"].append(stripped)

    if current:
        entries.append(current)

    return entries


# ── HTML Template ────────────────────────────────────────────────────────

def build_html(resume: dict) -> str:
    """Build resume HTML in a classic serif style (black/white, ruled headers).

    Company/role entries render as a bold company line with a right-aligned date
    and an italic role line, matching a traditional one-column resume layout.

    Args:
        resume: Parsed resume dict from parse_resume().

    Returns:
        Complete HTML string ready for PDF rendering.
    """
    import html as _html

    def esc(s: str) -> str:
        return _html.escape(str(s))

    sections = resume["sections"]

    def entries_html(section_key: str, label: str) -> str:
        if section_key not in sections:
            return ""
        items = ""
        for e in parse_entries(sections[section_key]):
            title = (e["title"] or "").strip()
            subtitle = (e["subtitle"] or "").strip()
            # The right-aligned slot is the dates: take the last "|"-segment so a
            # leading label (e.g. "Tech | Dec 2022 - Present") is dropped.
            right = esc([p.strip() for p in subtitle.split("|")][-1]) if subtitle else ""
            bullets = "".join(f"<li>{esc(b)}</li>" for b in e["bullets"])
            if " at " in title:
                role, company = title.split(" at ", 1)
                head = (f'<div class="row"><span class="l">{esc(company.strip())}</span>'
                        f'<span class="r">{right}</span></div>'
                        f'<div class="row sub"><span class="l">{esc(role.strip())}</span>'
                        f'<span class="r"></span></div>')
            else:
                head = (f'<div class="row"><span class="l">{esc(title)}</span>'
                        f'<span class="r">{right}</span></div>')
            items += f'<div class="entry">{head}<ul>{bullets}</ul></div>'
        return f'<div class="section">{label}</div>{items}'

    summary_html = ""
    if "SUMMARY" in sections:
        summary_html = f'<div class="section">Summary</div><div class="summary">{esc(sections["SUMMARY"].strip())}</div>'

    def fix_caps(label: str) -> str:
        # Restore acronym casing the LLM may have title-cased (Bi -> BI, Llm -> LLM).
        acro = {"bi", "ai", "llm", "ml", "qa", "sql", "api", "apis", "ci/cd", "rss", "mcp",
                "etl", "kpi", "kpis", "ux", "ui", "aws", "gcp"}
        return " ".join(w.upper() if w.lower() in acro else w for w in label.split())

    skills_html = ""
    if "TECHNICAL SKILLS" in sections:
        rows = "".join(
            f'<div class="skill"><span class="cat">{esc(fix_caps(cat))}:</span> {esc(val)}</div>'
            for cat, val in parse_skills(sections["TECHNICAL SKILLS"])
        )
        skills_html = f'<div class="section">Skills</div>{rows}'

    edu_html = ""
    if "EDUCATION" in sections:
        edu = esc(sections["EDUCATION"].strip()).replace("\n", "<br>")
        edu_html = f'<div class="section">Education</div><div class="edu">{edu}</div>'

    exp_html = entries_html("EXPERIENCE", "Work Experience")
    proj_html = entries_html("PROJECTS", "Projects")

    def linkify(part: str) -> str:
        p = part.strip()
        # Linkify domains/URLs (website, github) but never the email or phone.
        if "@" not in p and (p.startswith("http") or re.match(r"^[\w.-]+\.(com|io|dev|ai|org|net)(/.*)?$", p)):
            href = p if p.startswith("http") else "https://" + p
            return f'<a href="{esc(href)}" style="color:#000;text-decoration:none;">{esc(p)}</a>'
        return esc(p)

    contact = resume["contact"]
    contact_html = " &nbsp;|&nbsp; ".join(linkify(p) for p in contact.split("|")) if contact else ""

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'Times New Roman', Georgia, serif; font-size: 10.5pt; line-height: 1.32; color: #000; }}
.name {{ font-size: 22pt; font-weight: 700; letter-spacing: 0.3px; }}
.contact {{ font-size: 10pt; margin: 3px 0 6px; }}
.section {{ font-size: 11pt; font-weight: 700; text-transform: uppercase; letter-spacing: 0.6px;
           border-bottom: 1px solid #000; padding-bottom: 2px; margin: 13px 0 6px; }}
.row {{ display: flex; justify-content: space-between; align-items: baseline; }}
.row .l {{ font-weight: 700; }}
.row .r {{ font-weight: 700; }}
.sub .l, .sub .r {{ font-style: italic; font-weight: 400; }}
.entry {{ margin-bottom: 9px; break-inside: avoid; }}
ul {{ margin: 3px 0 0 0; padding-left: 15px; }}
li {{ margin-bottom: 2.5px; padding-left: 2px; }}
.summary {{ text-align: justify; }}
.skill {{ line-height: 1.4; }}
.skill .cat {{ font-weight: 700; }}
.edu {{ line-height: 1.4; }}
</style>
</head>
<body>
<div class="name">{esc(resume['name'])}</div>
<div class="contact">{contact_html}</div>
{summary_html}
{exp_html}
{proj_html}
{edu_html}
{skills_html}
</body>
</html>"""


# ── PDF Renderer ─────────────────────────────────────────────────────────

def render_pdf(html: str, output_path: str) -> None:
    """Render HTML to PDF using Playwright's headless Chromium.

    Args:
        html: Complete HTML string.
        output_path: Path to write the PDF file.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html, wait_until="networkidle")
        page.pdf(
            path=output_path,
            format="Letter",
            margin={"top": "0.5in", "right": "0.6in", "bottom": "0.5in", "left": "0.6in"},
            print_background=True,
        )
        browser.close()


# ── Public API ───────────────────────────────────────────────────────────

def convert_to_pdf(
    text_path: Path, output_path: Path | None = None, html_only: bool = False
) -> Path:
    """Convert a text resume/cover letter to PDF.

    Args:
        text_path: Path to the .txt file to convert.
        output_path: Optional override for the output path. Defaults to same
            name with .pdf extension.
        html_only: If True, output HTML instead of PDF.

    Returns:
        Path to the generated PDF (or HTML) file.
    """
    text_path = Path(text_path)
    text = text_path.read_text(encoding="utf-8")
    resume = parse_resume(text)
    html = build_html(resume)

    if html_only:
        out = output_path or text_path.with_suffix(".html")
        out = Path(out)
        out.write_text(html, encoding="utf-8")
        log.info("HTML generated: %s", out)
        return out

    out = output_path or text_path.with_suffix(".pdf")
    out = Path(out)
    render_pdf(html, str(out))
    log.info("PDF generated: %s", out)
    _render_docx_sibling(text, out.with_suffix(".docx"), kind="resume")
    return out


def _letter_html(text: str, applicant_name: str) -> str:
    """Build a simple, correctly-structured HTML letter.

    Cover letters have no resume structure (no SUMMARY line, no ALL-CAPS section
    headers), so parse_resume() drops their body. This renders the prose as
    paragraphs under a modest name header, escaping all content.
    """
    import html as _html

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    body = "\n".join(
        f"<p>{_html.escape(p).replace(chr(10), '<br>')}</p>" for p in paragraphs
    )
    name = _html.escape(applicant_name)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
@page {{ margin: 1in; }}
body {{ font-family: 'Calibri', 'Segoe UI', Arial, sans-serif; font-size: 11pt; line-height: 1.5; color: #222; }}
.name {{ font-size: 14pt; font-weight: 700; margin-bottom: 1.5em; }}
p {{ margin: 0 0 1em 0; }}
</style></head><body>
<div class="name">{name}</div>
{body}
</body></html>"""


def convert_letter_to_pdf(txt_path: Path, applicant_name: str,
                          output_path: Path | None = None) -> Path:
    """Render a cover-letter .txt to a properly formatted PDF."""
    txt_path = Path(txt_path)
    text = txt_path.read_text(encoding="utf-8")
    html = _letter_html(text, applicant_name)
    out = Path(output_path or txt_path.with_suffix(".pdf"))
    render_pdf(html, str(out))
    log.info("Cover letter PDF generated: %s", out)
    _render_docx_sibling(text, out.with_suffix(".docx"), kind="cover")
    return out


def _render_docx_sibling(text: str, out_path: Path, kind: str) -> Path | None:
    """Best-effort DOCX sibling generation for a rendered PDF."""
    try:
        from applypilot.scoring.docx_render import cover_to_docx, resume_to_docx

        if kind == "cover":
            return cover_to_docx(text, out_path)
        return resume_to_docx(text, out_path)
    except Exception as e:
        log.warning("DOCX generation failed for %s: %s", out_path, e)
        return None


def _applicant_name() -> str:
    try:
        from applypilot.config import load_profile

        return load_profile().get("personal", {}).get("full_name", "")
    except Exception:
        return ""


def batch_convert(limit: int = 50) -> int:
    """Convert .txt files in TAILORED_DIR that don't have corresponding PDFs.

    Scans for .txt files (excluding _JOB.txt and _REPORT.json), checks if a
    .pdf with the same stem already exists, and converts any that are missing.

    Args:
        limit: Maximum number of files to convert.

    Returns:
        Number of PDFs generated.
    """
    if not TAILORED_DIR.exists():
        log.warning("Tailored directory does not exist: %s", TAILORED_DIR)
        return 0

    txt_files = sorted(TAILORED_DIR.glob("*.txt"))
    # Exclude _JOB.txt and _CL.txt files from resume conversion
    # (they get their own conversion calls)
    candidates = [
        f for f in txt_files
        if not f.name.endswith("_JOB.txt")
    ]

    # Filter to those without a corresponding PDF or DOCX sibling
    to_convert: list[Path] = []
    for f in candidates:
        pdf_path = f.with_suffix(".pdf")
        docx_path = f.with_suffix(".docx")
        if not pdf_path.exists() or not docx_path.exists():
            to_convert.append(f)
        if len(to_convert) >= limit:
            break

    if not to_convert:
        log.info("All text files already have PDFs and DOCX siblings.")
        return 0

    log.info("Converting %d files to PDF/DOCX...", len(to_convert))
    converted = 0
    for f in to_convert:
        try:
            pdf_path = f.with_suffix(".pdf")
            if not pdf_path.exists():
                if f.name.endswith("_CL.txt"):
                    convert_letter_to_pdf(f, applicant_name=_applicant_name())
                else:
                    convert_to_pdf(f)
                converted += 1
            elif not f.with_suffix(".docx").exists():
                kind = "cover" if f.name.endswith("_CL.txt") else "resume"
                _render_docx_sibling(
                    f.read_text(encoding="utf-8"),
                    f.with_suffix(".docx"),
                    kind=kind,
                )
        except Exception as e:
            log.error("Failed to convert %s: %s", f.name, e)

    log.info("Done: %d/%d PDFs generated in %s", converted, len(to_convert), TAILORED_DIR)
    return converted
