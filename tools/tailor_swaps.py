"""
tailor_swaps.py — the swap-writer.

Bridges the tailoring AI to your REAL Word resume. Takes the actual swappable
paragraphs (from the classifier) plus a job, and returns SWAPS: for each paragraph
it tailors, the exact original text and the new text to replace it with. Feeds
straight into docx_editor.apply_swaps.

Philosophy: make the candidate the STRONGEST HONEST fit for the job. Reframe real
work sharply toward the role, but never claim work they didn't do. Change HOW
something is described, never WHAT was done — and never make it longer.
"""

import json
import re
import logging
import anthropic as anthropic_module
from anthropic import AsyncAnthropic
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type
)
from dotenv import load_dotenv

load_dotenv()
client = AsyncAnthropic()

_MODEL = "claude-sonnet-4-6"  # judgment-heavy writing; runs only on approved jobs


_SYSTEM_PROMPT = """You are an expert resume editor. Your goal: make this candidate \
look like the STRONGEST HONEST fit for the target job — an ideal candidate — while \
never claiming work they didn't actually do. You tailor their REAL resume paragraphs \
to the job.

WHAT YOU SHOULD DO (make them shine):
- Reframe real bullets to emphasize the angle most relevant to THIS job. Lead with
  what matters to this role.
- Mirror the job posting's terminology where the candidate genuinely has the skill
  (JD says "experimentation," anchor says "A/B testing" -> use "experimentation").
- Sharpen wording with strong action verbs. Keep every real metric exactly
  (30%, 98%, 100,000+ - never alter a number).
- Surface and reorder real skills to match the job's priorities.
- Rephrase confidently, as long as it describes the SAME real work, same tasks,
  same tools, same outcome.

THE HARD LINE (never cross it):
- The rewritten bullet must describe the SAME actual work the candidate did. Change
  HOW it's described, never WHAT was done.
- NEVER invent skills, tools, employers, responsibilities, or numbers not in the anchor.
- NEVER add a responsibility that wasn't there (no "led a team", no "partnered with
  engineering" if the original never said so).
- If the JD wants something the candidate lacks, DO NOT add it - leave it out and
  note it in gaps_left_honest.
  - PRESERVE concrete specifics and proper names from the original — methodology names
  (e.g. "DMAIC"), specific domains (e.g. "marketing and financial datasets"), named
  techniques. These ARE credibility. Never swap a specific term for a vaguer one just
  to fit a JD keyword. Add the keyword alongside the specific, or leave it.
- PRESERVE the candidate's personal voice and distinctive framing. If the summary or
  a bullet has a memorable personal angle (e.g. a psychology / human-behavior
  background), KEEP it — especially when the role touches human behavior, judgment,
  decision-making, or research. Do not sand the personality out to sound generic.

WRITING STYLE (critical - the current output reads too "AI"):
- Do NOT use em-dashes or the " - " construction to tack on extra clauses.
- No flowery add-on phrases like "enabling greater speed and confidence" or
  "analogous to...". Keep bullets concrete and plain, matching the original's style.
- Write like the original bullets: direct, factual, no filler.

LENGTH IS CRITICAL (the resume must stay exactly ONE page):
- Each rewritten bullet MUST be the SAME LENGTH OR SHORTER than the original - NEVER
  longer. Roughly match word count: if the original is 22 words, your rewrite is 22
  or fewer.
- If you cannot improve a bullet without making it longer, leave it UNCHANGED (omit
  it from swaps). Shorter and sharper always beats longer.
- Before returning, verify NO rewrite is longer than its original. Longer rewrites
  break the one-page layout and are unacceptable.

For SKILL lines: return the COMPLETE line in 'new', INCLUDING the "Category:" prefix
exactly as written in the original. Reorder/surface real skills; do not duplicate
or drop the category label, and do not add skills the candidate doesn't have.

- For SKILL lines specifically: the rewritten line must be the SAME LENGTH OR SHORTER
  than the original. You are REORDERING existing skills to surface the most relevant
  first — NOT adding skills. Adding terms lengthens the line and breaks the one-page
  layout. Same skills, better order, same or fewer characters.

Tailor only paragraphs where it genuinely helps; omit the rest.

OUTPUT — return ONLY valid JSON, no markdown, no preamble:
{
  "swaps": [
    {"original": "<exact original text, copied verbatim>", "new": "<tailored text, same length or shorter>", "kind": "summary|bullet|skill", "reason": "which JD requirement drove this"}
  ],
  "gaps_left_honest": ["JD requirements the candidate genuinely lacks — not faked"]
}"""


def _parse_json_response(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r'^```(?:json)?\n?', '', text)
        text = re.sub(r'\n?```$', '', text)
    text = re.sub(r'</?cite[^>]*>', '', text)
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
    reraise=True,
)


def _format_swappables(swappables: list[dict]) -> str:
    """Present the real paragraphs to the model. The '[kind]' tag is on its own,
    and the EXACT paragraph text follows on the next line — so when the model
    copies 'original', it copies only the real text, never our labels."""
    lines = []
    for item in swappables:
        kind = item["kind"]
        text = item["original"]
        lines.append(f'[{kind}]\n{text}')
    return "\n\n".join(lines)


@retry(**_RETRY)
async def tailor_to_swaps(swappables: list[dict], job_title: str, company: str,
                          job_description: str) -> dict:
    """Given the resume's real swappable paragraphs + a job, return swaps to apply.
    Returns {"swaps": [...], "gaps_left_honest": [...]}."""
    block = _format_swappables(swappables)

    user_message = f"""Tailor this candidate's REAL resume paragraphs to the job below.

=== TARGET JOB ===
Title: {job_title}
Company: {company}

Description:
{job_description[:4000]}

=== THE CANDIDATE'S ACTUAL RESUME PARAGRAPHS (tailor these) ===
{block}

For each paragraph worth tailoring for THIS job, return a swap with the EXACT
original text (copied verbatim so it can be matched) and your rewrite. Keep every
rewrite the SAME LENGTH OR SHORTER than the original — the resume must stay one page.
For skill lines, 'new' must be the COMPLETE line INCLUDING the "Category:" prefix.
Reframe sharply, plainly, no em-dashes, never claim work that wasn't done.
Return ONLY the JSON object."""

    response = await client.messages.create(
        model=_MODEL,
        max_tokens=3000,
        temperature=0.3,
        system=[{
            "type": "text",
            "text": _SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_message}],
    )

    return _parse_json_response(response.content[0].text)