"""
cover_letter.py — the pitch.

Writes a real cover letter for one job: grounded in actual facts about the company
(from research) and actual facts about the candidate (from their resume). Not
boilerplate — it makes a specific connection between what the company is doing and
what the candidate has genuinely done.

Honesty rules, same as everywhere: use only real experience from the resume, only
real facts from the research. No invented achievements, no generic gushing, no
claiming skills the candidate lacks. A specific, honest letter beats an
enthusiastic empty one.
"""

import json
import re
import logging
import anthropic as anthropic_module
from anthropic import AsyncAnthropic
from tenacity import (
    retry, stop_after_attempt, wait_exponential, retry_if_exception_type
)
from dotenv import load_dotenv

load_dotenv()
client = AsyncAnthropic()

_MODEL = "claude-sonnet-4-6"  # judgment-heavy writing; runs only on approved jobs

_SYSTEM_PROMPT = """You write cover-letter DRAFTS a candidate will lightly personalize before \
sending. In 2026 recruiters spend ~7 seconds on first pass and instantly spot generic AI \
writing, so the draft must be short, specific, and human.

CRITICAL — DO NOT RESTATE THE RESUME. The recruiter already has the resume with all its
metrics (percentages, record counts, etc.). Your letter must NOT re-list those numbers. Its
job is the STORY the resume can't tell: why this company, why this candidate, how they think
about the problem. If you find yourself quoting a metric from the resume, stop and write the
reasoning or motivation instead. At most ONE quantified detail, only if it's essential to a
point — and even then prefer framing over figures.

FORMAT (proper business letter):
- Start with: "Dear Hiring Manager," (or "Dear [Company] Hiring Team," if company name fits
  naturally). No date, no addresses.
- 3 short body paragraphs, ~220-280 words total.
- End with a simple closing line, then "Sincerely," on its own line, then the candidate's name
  on the next line.

THE 3 PARAGRAPHS:
- Para 1: ONE specific, researched reason for interest in THIS company (a real product,
  mission, or recent development). This is where specificity proves you did homework. Never
  "I am writing to express my interest."
- Para 2: HOW the candidate thinks about the problem this role solves — the mindset and
  approach they bring, connecting their background's THEME (not its metrics) to the role's core
  challenge. Reasoning, not resume bullets.
- Para 3: Why this role/company fits what they want to do next, and a confident, brief close.

HARD RULES:
- Do NOT restate resume metrics or list accomplishments. Story and reasoning only.
- Do NOT include any availability, start-date, or "available to start" language. None.
- Use only real facts (research) and real background themes (resume). Invent nothing; claim no
  skills the candidate lacks.
- Plain, direct, human voice. No clichés, no buzzwords, no em-dashes, vary sentence openings.
- Match the tone/framing of a sharp, tailored application — confident but not boastful.

Mark 1-2 spots with [PERSONALIZE: ...] where the candidate should add a genuine personal
sentence (a specific reason they care), keeping the letter authentically theirs.

OUTPUT — return ONLY valid JSON, no markdown, no preamble:
{
  "cover_letter": "the full letter, greeting to signed name, ~220-280 words, 1-2 [PERSONALIZE] markers",
  "hooks_used": ["the specific company facts / candidate themes you connected"],
  "notes": "one honest line on what to verify or personalize before sending"
}"""


def _parse_json_response(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r'^```(?:json)?\n?', '', text)
        text = re.sub(r'\n?```$', '', text)
    text = re.sub(r'</?cite[^>]*>', '', text)
    return json.loads(text.strip())


def _strip_dashes(text: str) -> str:
    """Deterministically kill the em-dashes the model loves despite instructions.
    Em-dash used as a clause break → comma/period; number-range en-dash → hyphen.
    This is the reliable enforcement; the prompt rule alone doesn't hold."""
    if not text:
        return text
    # Number ranges like "40–45%" or "40—45" → hyphen (matches your resume style)
    text = re.sub(r'(\d)\s*[—–]\s*(\d)', r'\1-\2', text)
    # Em/en dash used as a clause separator (spaces around it) → comma
    text = re.sub(r'\s+[—–]\s+', ', ', text)
    # Any stray remaining em/en dash → comma-space, just in case
    text = text.replace('—', ', ').replace('–', '-')
    # Tidy any doubled punctuation the replacements might create
    text = re.sub(r',\s*,', ',', text)
    text = re.sub(r'\s+,', ',', text)
    return text


_RETRY = dict(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((
        anthropic_module.RateLimitError,
        anthropic_module.APIConnectionError,
        anthropic_module.APITimeoutError,
        json.JSONDecodeError,
    )),
    reraise=True,
)


@retry(**_RETRY)
async def write_cover_letter(candidate_name: str, resume_text: str,
                             job_title: str, company: str, job_description: str,
                             research: dict | None = None) -> dict:
    """Write a cover letter for one job. Returns {cover_letter, hooks_used, notes}.

    resume_text: the candidate's resume as plain text (their real experience).
    research: the dict from research_company (what_they_do, recent_signal, angles).
              Optional — the letter is better with it, works without it."""
    research_block = "(no research provided)"
    if research:
        research_block = json.dumps(research, indent=2)

        user_message = f"""Write a cover-letter draft for this candidate and job.

=== CANDIDATE ===
Name: {candidate_name}

Their background (for THEMES and reasoning only — the resume already lists all metrics,
so do NOT re-quote numbers from here; use it to understand who they are and how they think):
{resume_text}

=== TARGET JOB ===
Title: {job_title}
Company: {company}

Description:
{job_description[:3500]}

=== COMPANY RESEARCH (use for the specific, researched opener) ===
{research_block}

Write the letter per your rules: proper greeting, 3 story-driven paragraphs that do NOT
restate resume metrics, no availability language, signed with the candidate's name.
Return ONLY the JSON object."""

    response = await client.messages.create(
        model=_MODEL,
        max_tokens=1500,
        temperature=0.4,   # a little warmth/variation for prose, still grounded
        system=[{
            "type": "text",
            "text": _SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_message}],
    )

    result = _parse_json_response(response.content[0].text)
    if "cover_letter" in result:
        result["cover_letter"] = _strip_dashes(result["cover_letter"])
    return result