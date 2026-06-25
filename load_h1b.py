"""
load_h1b.py — fills the sponsorship memory bank.

Reads the USCIS H-1B Employer Data Hub file, takes its ~99k unruly rows, and
wrangles them down to one well-behaved row per employer in a `sponsors` table.

Two things we keep deliberately UN-blended, because blending them lies to you:
  • New vs Continuation — "New" is a fresh petition (first-time hires, folks
    coming off OPT... you), "Continuation" is renewing someone already on staff.
    A company drowning in renewals but filing no new ones is NOT opening doors
    to new hires, even if its total looks juicy. New is your real signal.
  • 2025 vs 2026 — FY2026 here stops at Q2, so it's half a year cosplaying as a
    whole one. Shown apart, you read "still going" vs "gone quiet" honestly.

So we carry four atomic counts (new/cont × 2025/2026) and derive the rest.
Run once per data refresh; it rebuilds from scratch, no leftovers.
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

    `match_type` is a confidence label wearing a trenchcoat:
    exact > prefix > ambiguous > none. We store only the four atomic counts —
    new/continuation × 2025/2026 — and let the totals be computed on demand, so
    nothing can drift out of sync with the ground truth."""
    query: str                        # what we normalized and went hunting for
    match_type: str                   # "exact" | "prefix" | "ambiguous" | "none"
    raw_name: str | None = None       # the original legal name, in its Sunday best
    matched_key: str | None = None    # the normalized key we landed on
    new_2025: int = 0                 # fresh petitions, full year
    new_2026: int = 0                 # fresh petitions, partial (FY2026 thru Q2)
    cont_2025: int = 0                # renewals/extensions, full year
    cont_2026: int = 0                # renewals/extensions, partial
    candidates: list | None = None    # only filled in when things get murky

    # ── Derived views: never stored, always honest ──
    @property
    def total_new(self) -> int:
        """All fresh petitions across the window — YOUR signal."""
        return self.new_2025 + self.new_2026

    @property
    def total_cont(self) -> int:
        """All renewals across the window — context, not your signal."""
        return self.cont_2025 + self.cont_2026

    @property
    def total_approvals(self) -> int:
        """Everything, both types, both years. The headline number that can fib
        if read alone — which is exactly why we keep the parts."""
        return self.total_new + self.total_cont


def find_sponsor(conn, company_name):
    """Find a company in the sponsors table, seeing past its legal-name disguise.

    USCIS files everyone under their full Sunday-best legal name, so 'OpenAI'
    hides inside 'openai opco' and 'Ramp' inside 'ramp business'. Exact matching
    walks right past them, so we try, in descending order of trust:
        1. exact normalized match  — 'openai' == 'openai', no doubt about it
        2. token-prefix match      — 'openai' fronts 'openai opco', close enough

    The space in the LIKE pattern does heavy lifting: 'ramp %' nabs 'ramp
    business' but snubs 'rampart', because word boundaries keep us honest. If a
    short name fronts a whole crowd of unrelated employers, we throw up our hands
    and hand the lineup back for a human to pick from — no wild guessing."""
    target = normalize_name(company_name)

    rows = conn.execute(
        """
        SELECT employer_normalized, new_2025, new_2026, cont_2025, cont_2026, raw_name
        FROM sponsors
        WHERE employer_normalized = ?
           OR employer_normalized LIKE ? || ' %'
        """,
        (target, target),
    ).fetchall()

    if not rows:
        return SponsorMatch(query=target, match_type="none")

    # An exact hit always wins — the company filing under a clean name, no costume.
    for key, n25, n26, c25, c26, raw in rows:
        if key == target:
            return SponsorMatch(target, "exact", raw, key, n25, n26, c25, c26)

    # No exact hit, so everything left came in through the prefix door. The loader
    # grouped by employer_normalized, so each row is its own distinct beast.
    if len(rows) == 1:
        key, n25, n26, c25, c26, raw = rows[0]
        return SponsorMatch(target, "prefix", raw, key, n25, n26, c25, c26)

    # A whole gaggle starts with this name. Could be one company in two outfits,
    # could be total strangers. Not our call — lay them all out, let a human sort
    # the family from the lookalikes (never sum strangers).
    candidates = [
        SponsorMatch(target, "prefix", raw, key, n25, n26, c25, c26)
        for key, n25, n26, c25, c26, raw in rows
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

    # Fiscal year -> number too, so our == 2025 / == 2026 checks don't trip on strings
    df[COL_YEAR] = pd.to_numeric(df[COL_YEAR], errors="coerce")

    # The matchable key — every employer's true name under the costume
    df["employer_normalized"] = df[COL_EMPLOYER].apply(normalize_name)
    df = df[df["employer_normalized"] != ""]

    print(f"    {len(df):,} usable rows left standing after the bouncer's pass.")

    # Sort each count into its (type, year) cubby BEFORE grouping. .where(cond, 0)
    # keeps the value when the row matches that year, else zeroes it — so summing
    # each cubby later gives a clean per-type, per-year tally per employer.
    df["new_2025"]  = df[COL_NEW].where(df[COL_YEAR] == 2025, 0)
    df["new_2026"]  = df[COL_NEW].where(df[COL_YEAR] == 2026, 0)
    df["cont_2025"] = df[COL_CONT].where(df[COL_YEAR] == 2025, 0)
    df["cont_2026"] = df[COL_CONT].where(df[COL_YEAR] == 2026, 0)

    # Herd duplicate rows (one employer, many worksites) into a single tidy row
    # each — four atomic counts kept apart, no blending of type or year.
    grouped = df.groupby("employer_normalized").agg(
        new_2025=("new_2025", "sum"),
        new_2026=("new_2026", "sum"),
        cont_2025=("cont_2025", "sum"),
        cont_2026=("cont_2026", "sum"),
        raw_name=(COL_EMPLOYER, "first"),
    ).reset_index()

    # Keep only employers who got SOMEBODY across the line (any type, any year)
    total = (grouped["new_2025"] + grouped["new_2026"]
             + grouped["cont_2025"] + grouped["cont_2026"])
    grouped = grouped[total > 0]

    print(f"    {len(grouped):,} unique employers who actually sponsored someone.")

    # Write it into the DB, wiping the slate clean each run — no stale ghosts
    with _connect() as conn:
        conn.execute("DROP TABLE IF EXISTS sponsors")
        conn.execute("""
            CREATE TABLE sponsors (
                employer_normalized TEXT PRIMARY KEY,
                new_2025   INTEGER,
                new_2026   INTEGER,   -- partial: FY2026 through Q2 only
                cont_2025  INTEGER,
                cont_2026  INTEGER,   -- partial: FY2026 through Q2 only
                raw_name   TEXT
            )
        """)
        conn.executemany(
            "INSERT OR REPLACE INTO sponsors VALUES (?,?,?,?,?,?)",
            [
                (r.employer_normalized, int(r.new_2025), int(r.new_2026),
                 int(r.cont_2025), int(r.cont_2026), r.raw_name)
                for r in grouped.itertuples()
            ],
        )
        conn.commit()

    print(f"✅  Sponsorship memory bank stocked: {len(grouped):,} employers on file.\n")
    return grouped


def sanity_check_fuzzy():
    """Parade our target roster past the matcher and eyeball who shows up — now
    splitting NEW hires from RENEWALS, because new is the number that speaks to
    your odds. Reminder: 2026 is a HALF year (stops at Q2), so a dinky 2026
    figure isn't automatically a cold streak."""
    from config.companies import COMPANIES

    def _breakdown(m):
        return (f"new hires: {m.total_new} (2025: {m.new_2025}, 2026-to-date: {m.new_2026})"
                f"   |   renewals: {m.total_cont} (2025: {m.cont_2025}, 2026-to-date: {m.cont_2026})")

    print("\n🔎 Snooping through the H-1B table for our targets...\n")
    with _connect() as conn:
        for display_name, _slug in COMPANIES:
            m = find_sponsor(conn, display_name)
            if m.match_type == "exact":
                print(f"  ✅ {display_name:<18} → {m.raw_name}")
                print(f"        {_breakdown(m)}")
            elif m.match_type == "prefix":
                print(f"  🟢 {display_name:<18} → {m.raw_name} [caught on a prefix]")
                print(f"        {_breakdown(m)}")
            elif m.match_type == "ambiguous":
                print(f"  🟡 {display_name:<18} → {len(m.candidates)} lookalikes, you decide:")
                for c in m.candidates:
                    print(f"        - {c.raw_name}")
                    print(f"            {_breakdown(c)}")
            else:
                print(f"  ⬜ {display_name:<18} → not in the book "
                      f"(honest miss — might just not sponsor)")


if __name__ == "__main__":
    build_sponsors_table()
    sanity_check_fuzzy()