"""
research_company.py — the scout.

Before you write a cover letter, you want to know who you're writing to. This
tool sends Claude out with a web-search tool to dig up a LIGHT briefing on a
company + role: what they do, anything recent worth mentioning, and an honest
read on why this role might be open. Cheap by design — Haiku + a couple of
searches — because it runs on every approved job and we keep the bill small.

Honesty rule (same spirit as everywhere else): if the web doesn't say WHY a role
is open, the tool says "unclear" rather than inventing a reason. Cover letters
built on made-up facts get you caught; built on real ones, they land.
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

load_dotenv()
client = AsyncAnthropic()

# Haiku: research is "search + summarize," judgment-light, runs on every approved
# job — exactly the cheap-and-fast work Haiku is built for.
_MODEL = "claude-haiku-4-5-20251001"


_SYSTEM_PROMPT = """You are a sharp, fast company researcher prepping a candidate \
to write a cover letter. You have a web search tool — use it (1-3 searches) to \
ground your briefing in CURRENT facts, not assumptions.

Produce a LIGHT, honest briefing. Rules:
- Be concise. This feeds a cover letter, not a term paper.
- Base claims on what you actually find. If the web doesn't tell you something
  (especially WHY a role is open), say "unclear" — never invent it.
- Prefer recent, specific facts (a launch, a funding round, a product) over
  generic mission-statement fluff — specifics are what make a cover letter land.

Return ONLY valid JSON, no markdown, no preamble:
{
  "what_they_do": "1-2 sentences on the company's actual product/business",
  "recent_signal": "one recent, specific, mentionable fact (launch/funding/news), or 'nothing notable found'",
  "why_role_likely_open": "an honest, evidence-based guess (growth/backfill/new team), or 'unclear from public info'",
  "cover_letter_angles": ["2-3 specific, true hooks a cover letter could use"],
  "sources_note": "brief note on what you based this on"
}"""


def _parse_json_response(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r'^```(?:json)?\n?', '', text)
        text = re.sub(r'\n?```$', '', text)
    # The web-search tool wraps facts in <cite index="...">...</cite> tags.
    # Strip the tags but KEEP the text inside them — we want the facts, not the
    # citation scaffolding leaking into a cover letter.
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
    before_sleep=before_sleep_log(logger=logging.getLogger(__name__), exp_base=2) if False else None,
    reraise=True,
)
# (the before_sleep line above is finicky across tenacity versions; keep it simple:)
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
async def research_company(company: str, job_title: str, job_description: str = "") -> dict:
    """Research one company + role for cover-letter prep. Returns a dict:
      what_they_do, recent_signal, why_role_likely_open, cover_letter_angles, sources_note.

    Uses Claude's native web search, so it grounds in current facts. Cheap (Haiku)."""
    user_message = f"""Research this company and role so I can write a strong cover letter.

Company: {company}
Role: {job_title}

Role description (for context on what the team does):
{job_description[:1500]}

Search the web for current info, then return ONLY the JSON briefing specified."""

    response = await client.messages.create(
        model=_MODEL,
        max_tokens=1200,
        tools=[{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 3,   # cap searches so cost stays bounded
        }],
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    # With web search, the response has multiple blocks (search calls + text).
    # We want the final text block — that's where Claude's JSON briefing lands.
    text_parts = [b.text for b in response.content if b.type == "text"]
    full_text = "\n".join(text_parts).strip()
    return _parse_json_response(full_text)


# ── Standalone test ───────────────────────────────────────────────────────────
# Research one real company so we see it work before the agent uses it.
# Run from project root:  python -m tools.research_company
if __name__ == "__main__":
    import asyncio

    async def _test():
        print("\n🔍 Researching Anthropic / Data Scientist...\n")
        result = await research_company(
            company="Anthropic",
            job_title="Data Scientist, Supply",
            job_description="Optimize compute allocation across the inference fleet using user outcome metrics.",
        )
        print(json.dumps(result, indent=2))

    asyncio.run(_test())