from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


class RoleType(str, Enum):
    AI_ANALYST = "AI_Analyst"
    FORWARD_DEPLOY = "Forward_Deploy"
    DATA_ANALYST = "Data_Analyst"
    BUSINESS_ANALYST = "Business_Analyst"
    NONE = "None"


class VisaSignal(str, Enum):
    OPEN = "open"        # companies which sponsor
    CLOSED = "closed"    # the ones who mention they dont sponsor
    QUIET = "quiet"      # no mention either way
    AJAR = "ajar"        # ambiguous — slightly open, unclear 


class JobSource(str, Enum):
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    FANTASTIC = "fantastic"
    APIFY_LINKEDIN = "apify_linkedin"
    UNKNOWN = "unknown"      # fallback — should rarely happen, flags a pipeline gap


class JobInput(BaseModel):
    job_id: str
    title: str
    company: str
    location: str
    description: str
    job_url: str = ""
    posted_date: Optional[str] = None
    source: JobSource = JobSource.UNKNOWN


class JobScore(BaseModel):
    job_id: str
    score: int = Field(ge=0, le=100)
    role_type: RoleType
    visa_signal: VisaSignal
    reasoning: str
    apply: bool
    seniority_fit: bool
    keywords_matched: list[str] = []
    keywords_missing: list[str] = []
    source: JobSource = JobSource.UNKNOWN


class GapSeverity(str, Enum):
    BLOCKER = "blocker"        # JD lists it as hard-required, you don't have it
    COVERABLE = "coverable"    # missing, but framable / learnable / minor
    MINOR = "minor"            # nice-to-have, safe to ignore


class ResumeGap(BaseModel):
    job_id: str
    # Skills the JD wants AND you genuinely have — your ammunition.
    strengths: list[str]
    # Adjacent skills: "JD wants X, you have Y, that's a credible bridge."
    # Each entry is a short human-readable note, not just a keyword.
    transferable: list[str]
    # The gaps that actually block you — required things you can't claim.
    critical_gaps: list[str]
    # Minor / nice-to-have gaps not worth losing sleep over.
    minor_gaps: list[str]
    # Overall read of how big the gap is for THIS job.
    severity: GapSeverity
    # Real advice: what to emphasize, what to reframe, whether it's worth it.
    recommendation: str


class BatchJobInput(BaseModel):
    jobs: list[JobInput] = Field(min_length=1, max_length=10)


class BatchScoreOutput(BaseModel):
    scores: list[JobScore]
    total_jobs: int
    apply_count: int
    error_count: int