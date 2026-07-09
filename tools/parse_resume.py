"""
parse_resume.py — the intake interview.

Reads an uploaded resume (.docx), and asks Haiku to do two things at once:
  1. Extract a structured profile (education, skills, experience, projects...)
     matching the shape config/user.json expects.
  2. Produce a clean markdown version of the resume (used for cover-letter content).

Visa status is NOT parsed here — a resume doesn't state it. The caller passes it
in from the onboarding form.
"""

import json
import logging
from pathlib import Path

from anthropic import AsyncAnthropic
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import anthropic as anthropic_module
from dotenv import load_dotenv
from docx import Document

load_dotenv()
_client = AsyncAnthropic()
_MODEL = "claude-haiku-4-5-20251001"
logger = logging.getLogger(__name__)


def _docx_text(docx_path: str) -> str:
    """All paragraph text from the docx, in order — the raw material for parsing."""
    doc = Document(docx_path)
    lines = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    return "\n".join(lines)


_SYSTEM = """You are reading a candidate's resume text. Produce a JSON object with \
exactly two top-level keys: "profile" and "markdown".

"profile" is an object with these fields (fill what the resume supports; use an empty \
list or short string if the resume doesn't mention something — never invent):
  - name: string (the candidate's name)
  - education: array of strings (each: degree, school, years, GPA if present)
  - target_roles: array of strings (infer 2-4 likely target roles from their background)
  - technical_skills: array of strings (every concrete skill/tool/technology listed)
  - experience_years: short string (e.g. "0-2 (recent graduate)", "5+ years")
  - experience: array of strings (each: role, company, one-line impact)
  - projects: array of strings (each: project name and one-line description)
  - preferred_locations: array of strings (cities if stated, else empty)
  - acceptable_locations: array of strings (else empty)
  - preferred_industries: array of strings (infer from their background)

"markdown" is a clean, readable markdown rendering of the full resume — headings for \
each section, bullet points for accomplishments. Preserve the candidate's actual content \
and wording; do not embellish or add skills they didn't list. No em-dashes.

Return ONLY the JSON object. No preamble, no code fences."""


@retry(
    retry=retry_if_exception_type((anthropic_module.APIStatusError, anthropic_module.APIConnectionError)),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    stop=stop_after_attempt(4),
)
async def _call(text: str) -> dict:
    resp = await _client.messages.create(
        model=_MODEL,
        max_tokens=4000,
        system=_SYSTEM,
        messages=[{"role": "user", "content": text}],
    )
    raw = resp.content[0].text.strip()
    # Strip accidental code fences, then parse.
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)


async def parse_resume(docx_path: str) -> dict:
    """Parse a resume docx into {"profile": {...}, "markdown": "..."}.

    Raises on unreadable docx or unparseable model output — the caller decides how
    to surface that to the user."""
    text = _docx_text(docx_path)
    if len(text) < 50:
        raise ValueError("That resume looks empty or unreadable. Is it a real .docx?")
    result = await _call(text)
    if "profile" not in result or "markdown" not in result:
        raise ValueError("Couldn't structure that resume. Try a cleaner .docx.")
    return result


# ── Standalone test ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys, asyncio
    path = sys.argv[1] if len(sys.argv) > 1 else "Resume_Dharmil_Gandhi.docx"
    print(f"Parsing {path} ...")
    out = asyncio.run(parse_resume(path))
    print("\n--- PROFILE ---")
    print(json.dumps(out["profile"], indent=2))
    print("\n--- MARKDOWN (first 400 chars) ---")
    print(out["markdown"][:400])
