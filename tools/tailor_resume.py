"""
tailor_resume.py — the wardrobe.

Takes your ANCHOR resume (the master, always true) and a specific job, then
re-dresses it for that job: reorders to lead with what matters, mirrors the
posting's language where you genuinely have the skill, and shapes bullets toward
STAR + ATS best practice. Returns the tailored resume PLUS a change report, so
you see every alteration before it ever becomes a PDF.

The one unbreakable rule, hammered into the prompt: it may RE-EMPHASIZE and
REPHRASE what's true, but it must NEVER fabricate. No invented skills, no inflated
numbers, no experience you don't have. A gap stays a gap — the change report
names it rather than papering over it.
"""

import json
import re
import logging
import anthropic as anthropic_module
from anthropic import AsyncAnthropic
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type, before_sleep_log
)
from dotenv import load_dotenv

load_dotenv()   # ensure ANTHROPIC_API_KEY is in the environment before the client is built


logger = logging.getLogger(__name__)
client = AsyncAnthropic()

# Sonnet, not Haiku: tailoring is judgment-heavy writing, and it only runs on
# jobs you've already approved — so the quality is worth the cost.
_MODEL = "claude-sonnet-4-6"


_SYSTEM_PROMPT = """You are an expert resume editor specializing in ATS-optimized, \
STAR-method resumes for analytics, data, and AI roles. You tailor a candidate's \
ANCHOR resume to a specific job posting.

YOUR JOB:
- Reorder content so the most job-relevant experience and bullets come first.
- Rephrase bullets to mirror the posting's terminology — but ONLY when the candidate
  genuinely has that skill/experience already. (If the JD says "experimentation" and
  the anchor says "A/B testing," you may use "experimentation" — same real skill.)
- Shape bullets toward STAR: lead with a strong action verb, show the action, end
  with a quantified result. The candidate already has metrics — keep and surface them.
- Keep formatting ATS-safe: simple headers, no tables/columns/graphics, plain text,
  real keywords from the JD that the candidate actually possesses.

ABSOLUTE RULES (never break these):
- NEVER invent skills, tools, employers, titles, dates, or numbers not in the anchor.
- NEVER inflate a metric or claim seniority/experience the anchor doesn't support.
- If the JD requires something the candidate lacks, DO NOT add it. Leave the gap.
  Note it in the change report instead.
- Every word in the tailored resume must trace back to something true in the anchor.
- Preserve the candidate's real contact line, education, dates, and employers exactly.

OUTPUT — return ONLY valid JSON, no markdown, no preamble:
{
  "tailored_resume_markdown": "the full tailored resume in markdown",
  "change_report": [
    {"change": "what you changed", "reason": "which JD keyword/requirement drove it", "honest": true}
  ],
  "gaps_left_honest": ["JD requirements the candidate genuinely lacks — left out, not faked"],
  "ats_keywords_surfaced": ["real candidate skills now mirrored to match the JD's language"]
}"""


def _parse_json_response(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r'^```(?:json)?\n?', '', text)
        text = re.sub(r'\n?```$', '', text)
    return json.loads(text.strip())


_RETRY = dict(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((
        anthropic_module.RateLimitError,
        anthropic_module.APIConnectionError,
        anthropic_module.APITimeoutError,
        json.JSONDecodeError,
    )),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)


@retry(**_RETRY)
async def tailor_resume(anchor_markdown: str, job_title: str, company: str,
                        job_description: str,
                        keywords_matched: list[str] | None = None,
                        keywords_missing: list[str] | None = None) -> dict:
    """Tailor the anchor resume to one job. Returns a dict with:
      tailored_resume_markdown, change_report, gaps_left_honest, ats_keywords_surfaced."""
    matched = ", ".join(keywords_matched or []) or "(none provided)"
    missing = ", ".join(keywords_missing or []) or "(none provided)"

    user_message = f"""Tailor this candidate's anchor resume to the job below.

=== ANCHOR RESUME (the source of truth — never contradict it) ===
{anchor_markdown}

=== TARGET JOB ===
Title: {job_title}
Company: {company}

Description:
{job_description}

=== SCORER HINTS ===
Skills the candidate already matches: {matched}
Skills the JD wants that the candidate may lack: {missing}

Tailor the resume per your rules. Surface the matched skills using the JD's language;
do NOT fabricate the missing ones — leave those gaps and report them.
Return ONLY the JSON object specified."""

    response = await client.messages.create(
        model=_MODEL,
        max_tokens=4000,
        temperature=0.3,
        system=[{
            "type": "text",
            "text": _SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_message}],
    )

    return _parse_json_response(response.content[0].text)