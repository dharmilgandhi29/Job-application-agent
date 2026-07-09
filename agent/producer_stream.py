"""
producer_stream.py — the specialists, live.

Two independent case-file builders, each an async generator that yields progress
events for Server-Sent Events streaming. Split so each spends tokens ONLY on what
it produces:

  Mr. Fixer     -> tailor_resume_stream(job_id): resume only. NO company research.
  Mr. Wordsmith -> write_letter_stream(job_id): cover letter. Runs research (which
                   is what the letter needs), but nothing resume-related.

The terminal path (orchestrator.py) is untouched.

Each event: {"step": <name>, "status": "running"|"done"|"warn", "msg": <text>}
Final event adds: {"files": {...}, "changes": [...], "gaps": [...], "notes": <text>}
"""

import re
import sqlite3
from pathlib import Path

from tools.classify_resume import get_swappable
from tools.tailor_swaps import tailor_to_swaps
from tools.docx_editor import apply_swaps
from tools.docx_to_pdf import docx_to_pdf
from tools.research_company import research_company
from tools.cover_letter import write_cover_letter
from tools.letter_pdf import letter_to_pdf
from config.user import NAME, ANCHOR_DOCX

DB_PATH = "jobs.db"


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _job(job_id):
    with _db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def _safe_base(company, job_id):
    safe = "".join(c for c in company if c.isalnum() or c in " -_").strip().replace(" ", "_")
    return f"{safe}_{job_id}"


# Strip citation tags and stray markup from research text before it reaches the UI.
def _clean(text):
    if not text:
        return ""
    text = re.sub(r'</?cite[^>]*>', '', text)
    text = re.sub(r'<[^>]+>', '', text)
    return text.strip()


# ── Mr. Fixer: resume only, no research ──────────────────────────────────────
async def tailor_resume_stream(job_id):
    job = _job(job_id)
    if not job:
        yield {"step": "error", "status": "warn", "msg": "Couldn't find that case file."}
        return
    jd = job.get("description") or ""
    company, title = job["company"], job["title"]
    base = _safe_base(company, job_id)

    yield {"step": "prep", "status": "running", "msg": "Mr. Fixer's reading your resume..."}
    try:
        swappables = await get_swappable(ANCHOR_DOCX)
    except Exception as e:
        yield {"step": "prep", "status": "warn", "msg": f"Trouble reading your resume: {e}"}
        return
    yield {"step": "prep", "status": "done", "msg": "Got your resume in hand."}

    yield {"step": "tailor", "status": "running", "msg": "Reshaping it to fit this role..."}
    try:
        result = await tailor_to_swaps(swappables, title, company, jd)
        swaps = result.get("swaps", [])
    except Exception as e:
        yield {"step": "tailor", "status": "warn", "msg": f"Tailoring hit a snag: {e}"}
        return
    yield {"step": "tailor", "status": "done", "msg": f"Reworked {len(swaps)} lines to match."}

    yield {"step": "apply", "status": "running", "msg": "Rewriting the doc, keeping your formatting..."}
    out_docx = f"outputs/resume_{base}.docx"
    Path("outputs").mkdir(exist_ok=True)
    try:
        rep = apply_swaps(ANCHOR_DOCX, out_docx, swaps)
    except Exception as e:
        yield {"step": "apply", "status": "warn", "msg": f"Couldn't apply changes: {e}"}
        return
    yield {"step": "apply", "status": "done", "msg": f"{len(rep['applied'])} changes in, {len(rep['missed'])} skipped."}

    yield {"step": "pdf", "status": "running", "msg": "Printing it to PDF..."}
    pdf_path = None
    try:
        pdf_path = docx_to_pdf(out_docx)
        yield {"step": "pdf", "status": "done", "msg": "Resume PDF's ready."}
    except Exception as e:
        yield {"step": "pdf", "status": "warn", "msg": f"PDF export failed ({e}); the .docx is saved."}

    files = {"resume_docx": out_docx}
    if pdf_path:
        files["resume_pdf"] = pdf_path
    changes = [{"kind": s.get("kind"), "reason": s.get("reason", "")} for s in swaps]
    yield {
        "step": "done", "status": "done", "who": "fixer",
        "msg": "Resume's tailored, boss. Mr. Fixer kept it honest.",
        "files": files, "changes": changes,
        "gaps": result.get("gaps_left_honest", []),
    }


# ── Mr. Wordsmith: cover letter, with research ───────────────────────────────
async def write_letter_stream(job_id):
    job = _job(job_id)
    if not job:
        yield {"step": "error", "status": "warn", "msg": "Couldn't find that case file."}
        return
    jd = job.get("description") or ""
    company, title = job["company"], job["title"]
    base = _safe_base(company, job_id)
    research = None

    yield {"step": "prep", "status": "running", "msg": "Mr. Wordsmith's grabbing your resume..."}
    try:
        with open("resume.md") as f:
            resume_text = f.read()
    except Exception as e:
        yield {"step": "prep", "status": "warn", "msg": f"Trouble reading your resume: {e}"}
        return
    yield {"step": "prep", "status": "done", "msg": "Got it."}

    yield {"step": "research", "status": "running", "msg": f"Digging up the story on {company}..."}
    try:
        research = await research_company(company, title, jd)
        what = _clean(research.get("what_they_do") or "")
        yield {"step": "research", "status": "done", "msg": what[:140] if what else "Company checked out."}
    except Exception as e:
        yield {"step": "research", "status": "warn", "msg": f"Couldn't dig up much ({e}); pressing on."}

    yield {"step": "cover", "status": "running", "msg": "Writing your cover letter..."}
    cl_txt = cl_pdf = None
    notes = ""
    try:
        cl = await write_cover_letter(
            candidate_name=NAME, resume_text=resume_text, job_title=title,
            company=company, job_description=jd, research=research,
        )
        letter = cl.get("cover_letter", "")
        cl_txt = f"outputs/cover_letter_{base}.txt"
        with open(cl_txt, "w") as f:
            f.write(letter)
        clean = re.sub(r'\[PERSONALIZE:[^\]]*\]', '', letter)
        clean = re.sub(r'\n{3,}', '\n\n', clean).strip()
        cl_pdf = f"outputs/cover_letter_{base}.pdf"
        await letter_to_pdf(clean, cl_pdf)
        notes = _clean(cl.get("notes", "") or "")
        yield {"step": "cover", "status": "done", "msg": "Cover letter's drafted."}
    except Exception as e:
        yield {"step": "cover", "status": "warn", "msg": f"Cover letter failed ({e})."}
        return

    files = {}
    if cl_txt: files["cover_txt"] = cl_txt
    if cl_pdf: files["cover_pdf"] = cl_pdf
    yield {
        "step": "done", "status": "done", "who": "wordsmith",
        "msg": "Letter's ready, boss. Give it your voice before you send it.",
        "files": files, "notes": notes,
    }


# ── Do the Whole Thing: resume + research + letter, research shared ───────────
async def full_case_stream(job_id):
    job = _job(job_id)
    if not job:
        yield {"step": "error", "status": "warn", "msg": "Couldn't find that case file."}
        return
    jd = job.get("description") or ""
    company, title = job["company"], job["title"]
    base = _safe_base(company, job_id)
    research = None

    yield {"step": "prep", "status": "running", "msg": "Cracking my knuckles, reading your resume..."}
    try:
        swappables = await get_swappable(ANCHOR_DOCX)
        with open("resume.md") as f:
            resume_text = f.read()
    except Exception as e:
        yield {"step": "prep", "status": "warn", "msg": f"Trouble reading your resume: {e}"}
        return
    yield {"step": "prep", "status": "done", "msg": "Resume's on the desk."}

    # Research ONCE - shared by the letter (and it's the only step the resume skips).
    yield {"step": "research", "status": "running", "msg": f"Casing the joint at {company}..."}
    try:
        research = await research_company(company, title, jd)
        what = _clean(research.get("what_they_do") or "")
        yield {"step": "research", "status": "done", "msg": what[:140] if what else "Got the lay of the land."}
    except Exception as e:
        yield {"step": "research", "status": "warn", "msg": f"Couldn't dig up much ({e}); pressing on."}

    # Resume (Mr. Fixer)
    yield {"step": "tailor", "status": "running", "msg": "Mr. Fixer's reshaping your resume..."}
    try:
        result = await tailor_to_swaps(swappables, title, company, jd)
        swaps = result.get("swaps", [])
    except Exception as e:
        yield {"step": "tailor", "status": "warn", "msg": f"Tailoring hit a snag: {e}"}
        return
    yield {"step": "tailor", "status": "done", "msg": f"Reworked {len(swaps)} lines to fit."}

    yield {"step": "apply", "status": "running", "msg": "Rewriting the doc, formatting and all..."}
    out_docx = f"outputs/resume_{base}.docx"
    Path("outputs").mkdir(exist_ok=True)
    try:
        rep = apply_swaps(ANCHOR_DOCX, out_docx, swaps)
    except Exception as e:
        yield {"step": "apply", "status": "warn", "msg": f"Couldn't apply changes: {e}"}
        return
    yield {"step": "apply", "status": "done", "msg": f"{len(rep['applied'])} changes in, {len(rep['missed'])} skipped."}

    yield {"step": "pdf", "status": "running", "msg": "Running it through the printer..."}
    resume_pdf = None
    try:
        resume_pdf = docx_to_pdf(out_docx)
        yield {"step": "pdf", "status": "done", "msg": "Resume PDF's hot off the press."}
    except Exception as e:
        yield {"step": "pdf", "status": "warn", "msg": f"PDF export failed ({e}); .docx is saved."}

    # Cover letter (Mr. Wordsmith) - reuses the research above
    yield {"step": "cover", "status": "running", "msg": "Mr. Wordsmith's putting pen to paper..."}
    cl_txt = cl_pdf = None
    notes = ""
    try:
        cl = await write_cover_letter(
            candidate_name=NAME, resume_text=resume_text, job_title=title,
            company=company, job_description=jd, research=research,
        )
        letter = cl.get("cover_letter", "")
        cl_txt = f"outputs/cover_letter_{base}.txt"
        with open(cl_txt, "w") as f:
            f.write(letter)
        clean = re.sub(r'\[PERSONALIZE:[^\]]*\]', '', letter)
        clean = re.sub(r'\n{3,}', '\n\n', clean).strip()
        cl_pdf = f"outputs/cover_letter_{base}.pdf"
        await letter_to_pdf(clean, cl_pdf)
        notes = _clean(cl.get("notes", "") or "")
        yield {"step": "cover", "status": "done", "msg": "Letter's signed, sealed, ready."}
    except Exception as e:
        yield {"step": "cover", "status": "warn", "msg": f"Cover letter failed ({e})."}

    files = {"resume_docx": out_docx}
    if resume_pdf: files["resume_pdf"] = resume_pdf
    if cl_txt: files["cover_txt"] = cl_txt
    if cl_pdf: files["cover_pdf"] = cl_pdf
    changes = [{"kind": s.get("kind"), "reason": s.get("reason", "")} for s in swaps]
    yield {
        "step": "done", "status": "done", "who": "both",
        "msg": "Whole case file's built, boss. Give it a once-over before it goes out.",
        "files": files, "changes": changes,
        "gaps": result.get("gaps_left_honest", []), "notes": notes,
    }
