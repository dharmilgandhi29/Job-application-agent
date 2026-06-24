"""
load_h1b.py — fills the sponsorship memory bank.

Reads the USCIS H-1B Employer Data Hub file (FY2025 + FY2026), boils ~150k raw
rows down to one tidy row per employer, and stashes it in a `sponsors` table.

Because we only pulled the two most recent years, everything in here is recent
by construction — no decade-old IBM ghosts. Run this once per data refresh.
"""

import re
import sqlite3
import pandas as pd

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
    """Squash a messy legal name into a clean matchable key.
    'ANTHROPIC PBC' -> 'anthropic', '1X TECHNOLOGIES, INC.' -> '1x technologies'."""
    if not isinstance(raw, str):
        return ""
    name = raw.lower()
    name = re.sub(r"[.,&/]", " ", name)          # punctuation -> space
    name = re.sub(r"\s+", " ", name).strip()      # collapse whitespace
    # peel off trailing legal suffixes (possibly several: "foo inc llc")
    words = name.split()
    while words and words[-1] in _SUFFIXES:
        words.pop()
    return " ".join(words).strip()


def _connect():
    return sqlite3.connect(DB_PATH)


def build_sponsors_table():
    print("📖  Reading the H-1B file (this is a big one, give it a sec)...")
    df = pd.read_excel(XLSX_PATH, engine="openpyxl")
    df.columns = df.columns.str.strip()   # trim hidden trailing spaces from headers
    print(f"    {len(df):,} raw rows in the file.")

    # Keep only the columns we need
    df = df[[COL_YEAR, COL_EMPLOYER, COL_NEW, COL_CONT]].copy()

    # Toss the junk: blank/Null employer rows
    df = df[df[COL_EMPLOYER].notna()]
    df = df[df[COL_EMPLOYER].astype(str).str.strip().str.lower() != "null"]
    df = df[df[COL_EMPLOYER].astype(str).str.strip() != ""]

    # Approval counts -> numbers (anything weird becomes 0)
    df[COL_NEW] = pd.to_numeric(df[COL_NEW], errors="coerce").fillna(0)
    df[COL_CONT] = pd.to_numeric(df[COL_CONT], errors="coerce").fillna(0)
    df["approvals"] = df[COL_NEW] + df[COL_CONT]

    # The matchable key
    df["employer_normalized"] = df[COL_EMPLOYER].apply(normalize_name)
    df = df[df["employer_normalized"] != ""]

    print(f"    {len(df):,} usable rows after dropping junk.")

    # Squash duplicates (same employer, many worksites/years) into one row each:
    #   total_approvals  = summed across everything
    #   most_recent_year = latest fiscal year they had ANY approval
    #   raw_name         = one original spelling, for display
    grouped = df.groupby("employer_normalized").agg(
        total_approvals=("approvals", "sum"),
        most_recent_year=(COL_YEAR, "max"),
        raw_name=(COL_EMPLOYER, "first"),
    ).reset_index()

    # Only keep employers who actually got someone approved (>0)
    grouped = grouped[grouped["total_approvals"] > 0]

    print(f"    {len(grouped):,} unique employers with real approvals.")

    # Write it into the DB, fresh each run
    with _connect() as conn:
        conn.execute("DROP TABLE IF EXISTS sponsors")
        conn.execute("""
            CREATE TABLE sponsors (
                employer_normalized TEXT PRIMARY KEY,
                total_approvals     INTEGER,
                most_recent_year    INTEGER,
                raw_name            TEXT
            )
        """)
        conn.executemany(
            "INSERT OR REPLACE INTO sponsors VALUES (?,?,?,?)",
            [
                (r.employer_normalized, int(r.total_approvals),
                 int(r.most_recent_year), r.raw_name)
                for r in grouped.itertuples()
            ],
        )
        conn.commit()

    print(f"✅  Sponsorship memory bank loaded: {len(grouped):,} employers.\n")
    return grouped


def sanity_check():
    """Peek at whether OUR target companies actually matched. The real test of
    whether name-normalization is pulling its weight."""
    from config.companies import COMPANIES
    print("── Did our target companies match? ──")
    with _connect() as conn:
        for display_name, _slug in COMPANIES:
            key = normalize_name(display_name)
            row = conn.execute(
                "SELECT total_approvals, most_recent_year FROM sponsors WHERE employer_normalized = ?",
                (key,),
            ).fetchone()
            if row:
                print(f"  ✓ {display_name:<18} {row[0]:>5} approvals (latest FY{row[1]})")
            else:
                print(f"  ✗ {display_name:<18} no exact match (key tried: '{key}')")


if __name__ == "__main__":
    build_sponsors_table()
    sanity_check()