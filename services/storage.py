"""
storage.py — the system's long-term memory.

A little SQLite brain that remembers every job we've ever laid eyes on and what
we thought of it. Its whole reason for existing: so we never pay to score the
same job twice. See a job once, remember it forever, never get fooled again.
"""

import sqlite3
import json
from contextlib import contextmanager
from api.models import JobInput, JobScore

DB_PATH = "jobs.db"


@contextmanager
def _connect():
    """Open a connection, commit if all goes well, and always tidy up after
    ourselves — even if things go sideways mid-query. No leaked connections
    on our watch."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # let rows act like dicts: row["title"]
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _init_db():
    """Build the jobs table if it's not already there. Harmless to call every
    run — if the table exists, it just shrugs and moves on."""
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id        TEXT PRIMARY KEY,   -- the fingerprint: 'gh-123', 'ash-abc'
                title         TEXT,
                company       TEXT,
                location      TEXT,
                job_url       TEXT,
                source        TEXT,
                description   TEXT,
                -- the verdict (empty until the scorer weighs in)
                score         INTEGER,
                role_type     TEXT,
                visa_signal   TEXT,
                reasoning     TEXT,
                apply         INTEGER,            -- SQLite shuns bools, so 0/1 it is
                seniority_fit INTEGER,
                -- the visa oracle's read on the COMPANY (filled in by visa_intel)
                sponsor_status   TEXT,            -- new_hire_sponsor | renewals_only | no_record | unknown
                sponsor_new      INTEGER,         -- fresh H-1B petitions — the number that speaks to your odds
                sponsor_renewals INTEGER,         -- renewals — context, not your signal
                -- the JD-vs-history verdict (filled in by visa_intel's classifier)
                visa_verdict      TEXT,           -- machine-friendly category you can filter/sort by
                visa_verdict_note TEXT,           -- plain-English fact for when you're eyeballing a job
                -- where this job is in its life with us
                status        TEXT DEFAULT 'new', -- new | reviewed | approved | skipped | applied
                first_seen    TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)


def _migrate_add_sponsor_columns():
    """Bolt the visa-oracle columns onto an EXISTING jobs table without a fuss.

    SQLite's ADD COLUMN only works once — run it twice and it pitches a fit
    ('duplicate column name'). So we ask the table what it already has and only
    add what's missing. Safe to run on every import: does the work once on an
    old DB, shrugs politely ever after. Fresh DBs already got these from
    _init_db, so this just confirms they're there and moves on."""
    wanted = {
        "sponsor_status":   "TEXT",
        "sponsor_new":      "INTEGER",
        "sponsor_renewals": "INTEGER",
        "visa_verdict":      "TEXT",
        "visa_verdict_note": "TEXT",
    }
    with _connect() as conn:
        # PRAGMA table_info hands back one row per existing column; row["name"]
        # is the column's name. That's our "what's already here" checklist.
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
        for col, col_type in wanted.items():
            if col not in existing:
                # Column names + types are our own constants, never user input,
                # so the f-string here can't be SQL-injected. (ALTER TABLE won't
                # take ? placeholders for identifiers anyway.)
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {col_type}")
                print(f"🔧  Added '{col}' to the jobs table.")


# Make sure the brain exists — and has its visa columns — the moment anyone
# imports this module.
_init_db()
_migrate_add_sponsor_columns()


def get_seen_ids() -> set[str]:
    """Every job_id we already have on file. This is the 'have we met before?'
    list that keeps us from re-scoring old news."""
    with _connect() as conn:
        rows = conn.execute("SELECT job_id FROM jobs").fetchall()
    return {r["job_id"] for r in rows}


def filter_unseen(jobs: list[JobInput]) -> list[JobInput]:
    """Hand me freshly-discovered jobs, I hand back only the strangers — the
    ones we've never scored. The familiar faces get politely waved through
    without spending a single token on them."""
    seen = get_seen_ids()
    return [j for j in jobs if j.job_id not in seen]


def save_scored_job(job: JobInput, score: JobScore, visa, verdict: str, verdict_note: str):
    """Tuck a freshly-scored job into the memory bank, now with BOTH the visa
    oracle's read AND the JD-vs-history verdict riding along. INSERT OR IGNORE
    means a sneaky duplicate gets a polite shrug, not a tantrum.

    `visa` is a VisaIntel (duck-typed to keep storage.py's imports lean — we
    never import visa_intel here, which would knot up the import graph).
    `verdict` / `verdict_note` come from classify_visa_disagreement — the stable
    category you'll filter on later, plus the plain-English fact for your eyes."""
    with _connect() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO jobs (
                job_id, title, company, location, job_url, source, description,
                score, role_type, visa_signal, reasoning, apply, seniority_fit,
                sponsor_status, sponsor_new, sponsor_renewals,
                visa_verdict, visa_verdict_note, status
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            job.job_id, job.title, job.company, job.location, job.job_url,
            job.source.value, job.description,
            score.score, score.role_type.value, score.visa_signal.value,
            score.reasoning, int(score.apply), int(score.seniority_fit),
            visa.status, visa.new_hires, visa.renewals,
            verdict, verdict_note, "new",
        ))


def get_jobs(min_score: int = 0, status: str | None = None,
             apply_only: bool = False) -> list[dict]:
    """Fetch jobs back out of the brain for you to look at. Dial in a minimum
    score, a status, or 'only the ones worth applying to' — and they come back
    sorted best-first, freshest-first. Your personalized highlight reel."""
    query = "SELECT * FROM jobs WHERE score >= ?"
    params: list = [min_score]
    if status:
        query += " AND status = ?"
        params.append(status)
    if apply_only:
        query += " AND apply = 1"
    query += " ORDER BY score DESC, first_seen DESC"

    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def update_status(job_id: str, status: str):
    """Nudge a job along its journey: new → reviewed → approved → applied
    (or skipped, if it didn't make the cut). One small status change at a time."""
    with _connect() as conn:
        conn.execute("UPDATE jobs SET status = ? WHERE job_id = ?", (status, job_id))

# ── Application tracking: status + applied_date ──────────────────────────────
def _migrate_add_tracking_columns():
    """Add the application-tracking columns to an existing DB, once. Same safe
    pattern as the sponsor migration: check what's there, add only what's missing."""
    wanted = {
        "applied_date":   "TEXT",   # ISO date the user marked it applied
        "followup_date":  "TEXT",   # optional: when to nudge / follow up
        "notes_user":     "TEXT",   # the user's own notes on this application
    }
    with _connect() as conn:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
        for col, col_type in wanted.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {col_type}")
                print(f"🔧  Added '{col}' to the jobs table.")


_migrate_add_tracking_columns()


def set_status(job_id: str, status: str, applied_date: str | None = None,
               notes: str | None = None):
    """Move a job along its journey and optionally stamp when it was applied to
    and any user notes. Only updates the fields you pass."""
    sets = ["status = ?"]
    params: list = [status]
    if applied_date is not None:
        sets.append("applied_date = ?")
        params.append(applied_date)
    if notes is not None:
        sets.append("notes_user = ?")
        params.append(notes)
    params.append(job_id)
    with _connect() as conn:
        conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE job_id = ?", params)


def get_tracked() -> list[dict]:
    """Every job the user has actually acted on (applied / following up / interview
    / closed) — the application tracker's data. Freshest applications first."""
    active = ("applied", "following_up", "interview", "offer", "closed", "rejected")
    placeholders = ",".join("?" for _ in active)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM jobs WHERE status IN ({placeholders}) "
            f"ORDER BY applied_date DESC, first_seen DESC",
            active,
        ).fetchall()
    return [dict(r) for r in rows]
