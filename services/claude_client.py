import re
import json
import logging
import anthropic as anthropic_module
from anthropic import AsyncAnthropic
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type, before_sleep_log
)
from candidate_profile import CANDIDATE_PROFILE

logger = logging.getLogger(__name__)
client = AsyncAnthropic() 


# ── System prompt

def _build_system_prompt() -> str:
    p = CANDIDATE_PROFILE
    skills = ", ".join(p["technical_skills"])
    roles = ", ".join(p["target_roles"])
    preferred = ", ".join(p["preferred_locations"])
    acceptable = ", ".join(p["acceptable_locations"])
    industries = ", ".join(p["preferred_industries"])
    experience = " | ".join(p.get("experience", []))

    return f"""You are a precise job-fit scorer. Score postings for the candidate below and return ONLY valid JSON.

CANDIDATE:
Name: {p['name']}
Education: {" | ".join(p['education'])}
Visa: {p['visa_status']}
Experience level: {p['experience_years']}
Relevant experience: {experience}
Target roles: {roles}
Technical skills: {skills}
Preferred locations (full points): {preferred}
Acceptable locations (partial points): {acceptable}
Preferred industries: {industries}

SCORING RUBRIC (0-100):
- Role match (AI Analyst / Forward Deploy / DA / BA): 0-30
- Technical skills overlap with the posting: 0-25
- Seniority fit (entry to mid, 0-3 yrs): 0-15
- Location: preferred city = full 10, elsewhere in US or remote = 5, outside US = 0
- Industry relevance: 0-10
- Company quality / growth stage: 0-10

APPLY THRESHOLD:
- score >= 70 -> apply: true
- score 50-69 -> apply: true UNLESS visa_signal is "closed"
- score < 50  -> apply: false

VISA SIGNAL (read the posting, do not guess):
- open: explicitly sponsors / OPT-friendly / "will sponsor"
- closed: explicit refusal — "no sponsorship", "US citizens only", "must be authorized", "green card required"
- quiet: posting says nothing about visa or work authorization
- ajar: ambiguous or partial language, unclear either way

OUTPUT RULES (critical):
- Return ONLY valid JSON. No markdown, no preamble, no text outside the JSON.
- Base every judgment ONLY on the posting text provided. Never infer facts not present.
- If information is absent, reflect that (quiet/ajar, lower confidence) — do not invent it.
- reasoning: <= 40 words.
- keywords_matched: only skills that LITERALLY appear in BOTH the posting and the candidate's skills.
- keywords_missing: skills the posting requires that the candidate lacks. Max 10 each."""


_SYSTEM_PROMPT = _build_system_prompt()

_JSON_SCHEMA = """{
  "job_id": "string (echo back exactly what was given)",
  "score": 0-100,
  "role_type": "AI_Analyst|Forward_Deploy|Data_Analyst|Business_Analyst|None",
  "visa_signal": "open|closed|quiet|ajar",
  "reasoning": "<= 40 words",
  "apply": true|false,
  "seniority_fit": true|false,
  "keywords_matched": ["skills in both posting and candidate"],
  "keywords_missing": ["required skills candidate lacks"]
}"""


def _parse_json_response(raw: str) -> dict:
    """Strip accidental markdown fences, then parse.
    Raises JSONDecodeError on bad JSON, which the retry decorator treats as a retry trigger."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r'^```(?:json)?\n?', '', text)
        text = re.sub(r'\n?```$', '', text)
    return json.loads(text.strip())


# ── Shared retry config

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


# ── Single job scoring

@retry(**_RETRY)
async def score_single_job(job_id, title, company, location, cleaned_description, source="unknown"):
    user_message = f"""Score this job. Return JSON only.

Job ID: {job_id}
Title: {title}
Company: {company}
Location: {location}

Description:
{cleaned_description}

Return JSON matching exactly:
{_JSON_SCHEMA}"""

    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=450,
        temperature=0,
        system=[{
            "type": "text",
            "text": _SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_message}],
    )

    result = _parse_json_response(response.content[0].text)
    result["job_id"] = job_id
    result["source"] = source
    return result


# ── Batch scoring, up to 5 per call 

@retry(**_RETRY)
async def score_batch_jobs(jobs: list[dict]) -> list[dict]:
    """jobs: dicts with job_id, title, company, location, cleaned_description, source."""
    if not 1 <= len(jobs) <= 5:
        raise ValueError("Batch size must be 1-5")

    jobs_block = ""
    for i, job in enumerate(jobs, 1):
        jobs_block += f"""
--- JOB {i} (ID: {job['job_id']}) ---
Title: {job['title']}
Company: {job['company']}
Location: {job['location']}
Description:
{job['cleaned_description']}
"""

    user_message = f"""Score all {len(jobs)} jobs below. Return a JSON ARRAY, one object per job.

{jobs_block}

Each array element must match exactly:
{_JSON_SCHEMA}

Preserve each job_id exactly as given."""

    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=450 * len(jobs),
        temperature=0,
        system=[{
            "type": "text",
            "text": _SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_message}],
    )

    results = _parse_json_response(response.content[0].text)
    if not isinstance(results, list):
        results = [results]

    # Re-attach source per job by matching job_id (the model only echoes job_id)
    source_by_id = {j["job_id"]: j.get("source", "unknown") for j in jobs}
    for r in results:
        r["source"] = source_by_id.get(r.get("job_id"), "unknown")
    return results


# ── Resume gap analysis

@retry(**_RETRY)
async def analyze_resume_gap(job_id, title, company, cleaned_description):
    skills_str = ", ".join(CANDIDATE_PROFILE["technical_skills"])

    user_message = f"""Analyze how the candidate's skills line up against this job. Be useful, not literal.

CANDIDATE SKILLS: {skills_str}

JOB (ID: {job_id}):
{title} at {company}
{cleaned_description}

Think about: which required skills are real blockers vs nice-to-haves, where the candidate
has an ADJACENT skill that credibly transfers, and whether this job is worth tailoring for.

Return ONLY this JSON:
{{
  "job_id": "{job_id}",
  "strengths": ["skills the JD wants AND the candidate clearly has"],
  "transferable": ["short notes like 'JD wants Looker; candidate has Tableau + Power BI — credible bridge'"],
  "critical_gaps": ["REQUIRED skills the candidate genuinely lacks — real blockers only"],
  "minor_gaps": ["nice-to-have gaps safe to ignore"],
  "severity": "blocker|coverable|minor",
  "recommendation": "one honest sentence: worth applying/tailoring, and what to emphasize"
}}"""

    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        temperature=0,
        system=[{
            "type": "text",
            "text": _SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_message}],
    )

    result = _parse_json_response(response.content[0].text)
    result["job_id"] = job_id
    return result