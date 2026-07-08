"""
verify_h1b.py — a paranoid second opinion on the loader.

Reads the SAME xlsx, but counts one company's approvals the dumb, manual,
squint-at-it way — no normalize_name, no groupby, no .where(). If this matches
what load_h1b.py reported, the loader's logic holds. If not, we caught a bug.
Throwaway — not part of the pipeline.
"""

import pandas as pd

XLSX_PATH = "data/h1b/Employer Information.xlsx"
COL_YEAR = "Fiscal Year"
COL_EMPLOYER = "Employer (Petitioner) Name"
COL_NEW = "New Employment Approval"
COL_CONT = "Continuation Approval"

# Change this to whichever company you want to audit. Use a chunk of the
# LEGAL name as it appears in the file (uppercase), from your sanity-check output.
NEEDLE = "SNOWFLAKE"

df = pd.read_excel(XLSX_PATH, engine="openpyxl")
df.columns = df.columns.str.strip()

# Numbers as numbers
df[COL_NEW] = pd.to_numeric(df[COL_NEW], errors="coerce").fillna(0)
df[COL_CONT] = pd.to_numeric(df[COL_CONT], errors="coerce").fillna(0)
df[COL_YEAR] = pd.to_numeric(df[COL_YEAR], errors="coerce")

# Grab every row whose employer name contains our needle (case-insensitive).
# This is the deliberately-dumb match — it'll catch SNOWFLAKE INC and anything
# else with 'snowflake' in it, so you SEE exactly what's being counted.
hits = df[df[COL_EMPLOYER].astype(str).str.upper().str.contains(NEEDLE, na=False)]

print(f"\n🔎 Raw rows in the file matching '{NEEDLE}':  {len(hits)}\n")

# Show the actual rows so you can eyeball them — names, years, both counts.
cols = [COL_EMPLOYER, COL_YEAR, COL_NEW, COL_CONT]
with pd.option_context("display.max_rows", None, "display.width", None):
    print(hits[cols].to_string(index=False))

# Now the manual tally, split exactly how the loader claims to split it.
for yr in (2025, 2026):
    rows = hits[hits[COL_YEAR] == yr]
    print(f"\n── FY{yr} ──")
    print(f"   new (sum of '{COL_NEW}'):  {int(rows[COL_NEW].sum())}")
    print(f"   cont (sum of '{COL_CONT}'): {int(rows[COL_CONT].sum())}")

print(f"\n── across both years ──")
print(f"   total new:  {int(hits[hits[COL_YEAR].isin([2025,2026])][COL_NEW].sum())}")
print(f"   total cont: {int(hits[hits[COL_YEAR].isin([2025,2026])][COL_CONT].sum())}")
print()
