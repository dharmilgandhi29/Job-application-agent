"""
load_h1b.py — fills the sponsorship memory bank.

Reads the USCIS H-1B Employer Data Hub file, takes its ~99k unruly rows, and
wrangles them down to one well-behaved row per employer in a `sponsors` table.

We keep 2025 and 2026 in SEPARATE bunks, never bunked together — because FY2026
in this file quits early at Q2, so it's half a year cosplaying as a whole one.
Show the two side by side and a human can tell "still going strong" from "gone
quiet" instead of getting bamboozled by a runt of a half-year. Run once per
data refresh and it rebuilds itself from scratch, no leftovers.
"""

import re
import sqlite3
import pandas as pd
from dataclasses import dataclass

XLSX_PATH = "data/h1b/Employer Information.xlsx"
DB_PATH = "jobs.db"

# The only columns we actually care about (exact header text from the file).
COL_YEAR = "Fiscal Year"
COL_EMPLOYER = "Employer (Petitioner) Name"
COL_NEW = "New Employment Approval"
COL_CONT = "Continuation Approval"

# Legal suffixes / noise words to strip so "ANTHROPIC PBC" matches "Anthropic".
_SUFFIXES = [
    "inc", "incorporated", "llc", "l.l.c", "corp", "corporation", "co",
    "pbc", "lp", "llp", "ltd", "limited", "plc", "gmbh", "company",
]


def normalize_name(raw: str) -> str:
    """Squash a messy legal name into a clean matchable key. Strips the costume
    so the company underneath is recognizable.
    'ANTHROPIC PBC' -> 'anthropic', '1X TECHNOLOGIES, INC.' -> '1x technologies'."""
    if not isinstance(raw, str):
        return ""
    name = raw.lower()
    name = re.sub(r"[.,&/]", " ", name)          # punctuation -> space
    name = re.sub(r"\s+", " ", name).strip()      # collapse the whitespace gremlins
    # peel off trailing legal suffixes (possibly several stacked up: "foo inc llc")
    words = name.split()
    while words and words[-1] in _SUFFIXES:
        words.pop()
    return " ".join(words).strip()


@dataclass
class SponsorMatch:
    """The loot from rummaging through the H-1B table for one employer.

    `match_type` is really a confidence label wearing a trenchcoat:
    exact > prefix > ambiguous > none. The two year fields sleep in separate
    beds on purpose — 2026 is a half-year, so we never let it sneak into a sum."""
    query: str                        # what we normalized and went hunting for
    match_type: str                   # "exact" | "prefix" | "ambiguous" | "none"
    raw_name: str | None = None       # the original legal name, in its Sunday best
    matched_key: str | None = None    # the normalized key we landed on
    approvals_2025: int = 0           # full fiscal year, the whole pie
    approvals_2026: int = 0           # partial — only FY2026 through Q2, a slice
    total_approvals: int = 0          # 2025 + 2026-to-date, for bucketing later
    candidates: list | None = None    # only filled in when things get murky (ambiguous)


def find_sponsor(conn, company_name):
    """Find a company in the sponsors table, seeing past its legal-name disguise.

    USCIS files everyone under their full Sunday-best legal name, so 'OpenAI'
    is hiding inside 'openai opco' and 'Ramp' inside 'ramp business'. Exact
    matching walks right past them, so we try, in descending order of trust:
        1. exact normalized match  — 'openai' == 'openai', no doubt about it
        2. token-prefix match      — 'openai' fronts 'openai opco', close enough

    The space in the LIKE pattern is doing heavy lifting: 'ramp %' nabs
    'ramp business' but snubs 'rampart', because word boundaries keep us honest.
    If a short name fronts a whole crowd of unrelated employers, we throw up our
    hands and hand the lineup back for a human to pick from — no wild guessing."""
    target = normalize_name(company_name)

    rows = conn.execute(
        """
        SELECT employer_normalized, approvals_2025, approvals_2026,
               total_approvals, raw_name
        FROM sponsors
        WHERE employer_normalized = ?
           OR employer_normalized LIKE ? || ' %'
        """,
        (target, target),
    ).fetchall()

    if not rows:
        return SponsorMatch(query=target, match_type="none")

    # An exact hit always wins — that's the company filing under a clean name,
    # no costume, no ambiguity.
    for key, a25, a26, total, raw in rows:
        if key == target:
            return SponsorMatch(target, "exact", raw, key, a25, a26, total)

    # No exact hit, so everything left came in through the prefix door. The
    # loader grouped by employer_normalized, so each row is its own distinct beast.
    if len(rows) == 1:
        key, a25, a26, total, raw = rows[0]
        return SponsorMatch(target, "prefix", raw, key, a25, a26, total)

    # A whole gaggle of employers start with this name. Could be one company in
    # two outfits, could be three total strangers. Not our call — we lay them all
    # out and let a human sort the family from the lookalikes (never sum strangers).
    candidates = [
        SponsorMatch(target, "prefix", raw, key, a25, a26, total)
        for key, a25, a26, total, raw in rows
    ]
    return SponsorMatch(target, "ambiguous", candidates=candidates)


def _connect():
    return sqlite3.connect(DB_PATH)


def build_sponsors_table():
    print("📖  Cracking open the H-1B tome (it's a chonky one, hang tight)...")
    df = pd.read_excel(XLSX_PATH, engine="openpyxl")
    df.columns = df.columns.str.strip()   # shave the hidden trailing spaces off the headers
    print(f"    {len(df):,} raw rows tumbled out.")

    # Keep only the columns we actually came for
    df = df[[COL_YEAR, COL_EMPLOYER, COL_NEW, COL_CONT]].copy()

    # Show the junk the door: blank/Null employer rows aren't invited
    df = df[df[COL_EMPLOYER].notna()]
    df = df[df[COL_EMPLOYER].astype(str).str.strip().str.lower() != "null"]
    df = df[df[COL_EMPLOYER].astype(str).str.strip() != ""]

    # Approval counts -> numbers (anything weird gets benched at 0)
    df[COL_NEW] = pd.to_numeric(df[COL_NEW], errors="coerce").fillna(0)
    df[COL_CONT] = pd.to_numeric(df[COL_CONT], errors="coerce").fillna(0)
    df["approvals"] = df[COL_NEW] + df[COL_CONT]

    # Fiscal year -> number too, so our == 2025 / == 2026 checks don't trip over strings
    df[COL_YEAR] = pd.to_numeric(df[COL_YEAR], errors="coerce")

    # The matchable key — every employer's true name under the costume
    df["employer_normalized"] = df[COL_EMPLOYER].apply(normalize_name)
    df = df[df["employer_normalized"] != ""]

    print(f"    {len(df):,} usable rows left standing after the bouncer's pass.")

    # Sort each approval into its year's bucket BEFORE grouping. .where(cond, 0)
    # keeps the count when the row belongs to that year, else zeroes it out — so
    # summing each column later gives a clean per-year tally per employer.
    df["appr_2025"] = df["approvals"].where(df[COL_YEAR] == 2025, 0)
    df["appr_2026"] = df["approvals"].where(df[COL_YEAR] == 2026, 0)

    # Herd all the duplicate rows (one employer, many worksites) into a single
    # tidy row each — two years kept apart, plus a combined total for bucketing.
    grouped = df.groupby("employer_normalized").agg(
        approvals_2025=("appr_2025", "sum"),
        approvals_2026=("appr_2026", "sum"),
        raw_name=(COL_EMPLOYER, "first"),
    ).reset_index()
    grouped["total_approvals"] = grouped["approvals_2025"] + grouped["approvals_2026"]

    # Only keep employers who actually got somebody across the line (>0)
    grouped = grouped[grouped["total_approvals"] > 0]

    print(f"    {len(grouped):,} unique employers who actually sponsored someone.")

    # Write it into the DB, wiping the slate clean each run — no stale ghosts
    with _connect() as conn:
        conn.execute("DROP TABLE IF EXISTS sponsors")
        conn.execute("""
            CREATE TABLE sponsors (
                employer_normalized TEXT PRIMARY KEY,
                approvals_2025      INTEGER,
                approvals_2026      INTEGER,   -- partial: FY2026 through Q2 only
                total_approvals     INTEGER,
                raw_name            TEXT
            )
        """)
        conn.executemany(
            "INSERT OR REPLACE INTO sponsors VALUES (?,?,?,?,?)",
            [
                (r.employer_normalized, int(r.approvals_2025),
                 int(r.approvals_2026), int(r.total_approvals), r.raw_name)
                for r in grouped.itertuples()
            ],
        )
        conn.commit()

    print(f"✅  Sponsorship memory bank stocked: {len(grouped):,} employers on file.\n")
    return grouped


def sanity_check_fuzzy():
    """Parade our target roster past the matcher and eyeball who shows up, now
    with both years laid out side by side. Reminder to self: 2026 is a HALF
    year (stops at Q2), so a dinky 2026 number isn't automatically a cold streak."""
    from config.companies import COMPANIES

    def _split(m):
        return (f"{m.total_approvals} total "
                f"(2025: {m.approvals_2025}, 2026-to-date: {m.approvals_2026})")

    print("\n🔎 Snooping through the H-1B table for our targets...\n")
    with _connect() as conn:
        for display_name, _slug in COMPANIES:
            m = find_sponsor(conn, display_name)
            if m.match_type == "exact":
                print(f"  ✅ {display_name:<18} → {m.raw_name}")
                print(f"        {_split(m)}")
            elif m.match_type == "prefix":
                print(f"  🟢 {display_name:<18} → {m.raw_name} [caught on a prefix]")
                print(f"        {_split(m)}")
            elif m.match_type == "ambiguous":
                print(f"  🟡 {display_name:<18} → {len(m.candidates)} lookalikes, you decide:")
                for c in m.candidates:
                    print(f"        - {c.raw_name}: {_split(c)}")
            else:
                print(f"  ⬜ {display_name:<18} → not in the book "
                      f"(honest miss — might just not sponsor)")


if __name__ == "__main__":
    build_sponsors_table()
    sanity_check_fuzzy()