import re


# Boilerplate patterns to strip before sending a JD to Claude.
# Pure cost optimization: removes legally-required filler, benefits brochures,
# and application instructions that carry zero signal for fit scoring.
# Roughly halves input tokens per job. Written conservatively — better to leave
# a little fat than to accidentally cut a real requirement.
_STRIP_PATTERNS = [
    # Equal opportunity / DEI / legal boilerplate
    r'(?si)(we are an equal opportunity|equal employment opportunity|affirmative action|eeo|eeoc).{0,400}?(?=\n\n|\Z)',
    r'(?si)(we are committed to (diversity|inclusion|creating a diverse|building a diverse)).{0,300}?(?=\n\n|\Z)',
    r'(?si)(accommodation[s]? for (disabilities|applicants with)).{0,200}?(?=\n|\Z)',
    r'(?si)(pursuant to|in accordance with|compliance with).{0,150}?(law|act|regulation).{0,100}?(?=\n|\Z)',

    # Benefits / perks sections
    r'(?si)(we offer|our benefits|benefits include|perks include|what we offer)\s*:?.{0,600}?(?=\n\n|\Z)',
    r'(?i)(health|dental|vision) insurance.*?(?=\n|\Z)',
    r'(?i)(401k|401\(k\)|retirement plan|pto|paid time off|unlimited pto|parental leave).*?(?=\n|\Z)',

    # Salary / compensation disclosure lines
    r'(?i)(salary|compensation|pay) range\s*:.*?(?=\n|\Z)',
    r'\$[\d,]+\s*[-–to]+\s*\$[\d,]+\s*(per year|annually|\/yr|per hour|\/hr)?.*?(?=\n|\Z)',

    # Application-instruction filler
    r'(?i)(to apply,?\s*(please|submit|send|click|visit)).*?(?=\n|\Z)',
    r'(?i)(background check|drug (screen|test)|e-verify).*?(?=\n|\Z)',
]


def clean_job_description(raw: str) -> str:
    """
    Strip boilerplate from a raw job description and cap its length.

    Returns cleaned text, max 3000 chars. The cap is a backstop so a single
    pathological posting can't blow the token budget — real JDs land well under it.
    """
    text = raw

    for pattern in _STRIP_PATTERNS:
        text = re.sub(pattern, '', text)

    # Collapse the whitespace the deletions leave behind
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    text = text.strip()

    # Hard backstop — scoring never needs more than this
    if len(text) > 3000:
        text = text[:3000] + "\n[description truncated]"

    return text