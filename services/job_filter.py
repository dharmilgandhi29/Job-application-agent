"""
Tier 1 pre-filter: keep only jobs whose TITLE matches the target roles.

This runs BEFORE any Claude scoring — it's free text matching that cuts thousands
of raw jobs down to the relevant slice, so we only spend tokens scoring real candidates.

Philosophy: role-matching ONLY. Nothing about visa or employer here — visa is handled
later by the scorer as a tag (open/closed/quiet/ajar), never as a filter. We want every
job for our roles surfaced; the human decides on borderline visa cases.

Deliberately generous: better to keep a borderline title and let the smart scorer judge it
than to drop something good at the dumb-keyword stage.
"""

from api.models import JobInput

# Title keywords that define "a role worth scoring."
# Tighter than before: "analyst" is the workhorse; AI/ML must pair with a role
# word, not match bare "ai" everywhere. Lowercased, substring-matched.
TARGET_TITLE_KEYWORDS = [
    # The core — "analyst" catches Data/Business/BI/Reporting/Analytics Analyst, etc.
    "analyst",
    "analytics",
    "business intelligence",
    # AI/ML but ONLY when paired with analyst/engineer/scientist-type role words
    "ai analyst", "ai engineer", "applied ai", "ai/ml", "machine learning",
    "data and ai", "ai data",
    # Forward-deployed / solutions
    "forward deployed", "forward deployment", "solutions engineer",
    # Data roles (broad, scorer sorts borderline)
    "data scientist", "data science",
]

# Reject these even if a keyword matches — wrong roles or too senior.
TITLE_BLOCKLIST = [
    # Wrong functions
    "account executive", "sales", "recruiter", "recruiting", "marketing",
    "designer", "attorney", "counsel", "compliance", "policy",
    "data center", "datacenter",
    # Too senior for an entry/early-career search
    "architect", "principal", "staff", "director", "head of",
    "vp ", "vice president", "lead ", "manager", "senior staff",
    # Research/safety roles that aren't analyst roles
    "fellow", "research scientist", "safety", "security",
]

def passes_title_filter(title: str) -> bool:
    """True if the title matches a target role and isn't on the blocklist."""
    t = title.lower()

    # Reject blocklisted titles first
    if any(bad in t for bad in TITLE_BLOCKLIST):
        return False

    # Keep if any target keyword appears
    return any(kw in t for kw in TARGET_TITLE_KEYWORDS)



def filter_jobs(jobs: list[JobInput]) -> list[JobInput]:
    """Filter a list of jobs down to those whose titles match target roles."""
    return [job for job in jobs if passes_title_filter(job.title)]



# US state abbreviations + common US location signals.
# Used to KEEP US jobs. Matching is permissive — when in doubt, keep the job
# and let the scorer judge, rather than risk dropping a real US role.
_US_STATES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA",
    "KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
    "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT",
    "VA","WA","WV","WI","WY","DC",
}

_US_SIGNALS = [
    "united states", "u.s.", "usa", "u.s.a", "remote - us", "remote, us",
    "remote (us", "us remote", "remote us",
]

# If a location clearly names a non-US country, drop it.
_NON_US_SIGNALS = [
    "canada", "united kingdom", "uk", "london", "ireland", "dublin",
    "australia", "sydney", "germany", "berlin", "france", "paris",
    "india", "bangalore", "singapore", "netherlands", "amsterdam",
    "spain", "japan", "tokyo", "emea", "apac", "latam",
]


def is_us_location(location: str) -> bool:
    """
    Permissive US check. Returns True if the location looks US-based OR is
    ambiguous (we keep ambiguous ones and let the scorer decide). Returns
    False only when it clearly names a non-US place with no US signal.
    """
    if not location:
        return True  # no location given → keep, let scorer see it

    loc = location.lower()

    # Positive US signals → keep
    if any(sig in loc for sig in _US_SIGNALS):
        return True
    # A US state abbreviation as a word (", CA", ", NY") → keep
    if any(f", {st.lower()}" in loc or f"{st.lower()}," in loc for st in _US_STATES):
        return True
    if "remote" in loc and not any(bad in loc for bad in _NON_US_SIGNALS):
        return True  # bare "Remote" with no foreign tag → assume US-eligible

    # Clear non-US signal and no US signal above → drop
    if any(bad in loc for bad in _NON_US_SIGNALS):
        return False

    # Ambiguous → keep, let the scorer judge
    return True


def filter_us_only(jobs: list[JobInput]) -> list[JobInput]:
    """Keep only jobs that look US-based (permissive — keeps ambiguous ones)."""
    return [job for job in jobs if is_us_location(job.location)]


