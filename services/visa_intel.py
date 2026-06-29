"""
visa_intel.py — the sponsorship oracle.

You hand it a company name; it goes spelunking through the H-1B `sponsors` table
(built by load_h1b.py) and hands back an honest read on whether this place
actually sponsors PEOPLE LIKE YOU — fresh hires, not just renewals of folks
they've already got on the books.

Two stubborn refusals baked in, both earned the hard way:
  • We do NOT bucket by size ("strong" vs "weak"). Those cutoffs are made-up.
    We label by STRUCTURE instead — do they file NEW petitions or only renew? —
    which is a fact, not a vibe. The raw counts ride shotgun so you judge size.
  • We do NOT sum lookalike companies. If a name is a hall of mirrors (hi,
    Sierra), we say so and hand back the suspects rather than inventing a number.

Normalization + matching are imported from load_h1b on purpose: the exact same
function that WROTE the keys has to READ them, or lookups quietly ghost us. One
source of truth, no drift.
"""

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass

# The same matching brain that built the table — imported, never re-typed, so
# write-time and read-time normalization can't drift apart behind our backs.
from load_h1b import normalize_name, find_sponsor, SponsorMatch

DB_PATH = "jobs.db"


# ── Curated tie-breakers for the ambiguous handful ───────────────────────────
# When a short name fronts a whole crowd of unrelated employers, find_sponsor
# honestly throws up its hands. For OUR specific roster we DO know which face in
# the lineup is the real one (verified by eyeballing the new-vs-renewal split),
# so we pin them. Keyed by normalized display name -> the legal-name key to grab.
# Sierra is deliberately NOT here: no candidate was clearly sierra.ai, so we let
# it stay honestly "unknown" rather than finger the wrong suspect.
COMPANY_ALIASES = {
    "cohere":  "cohere us",            # the AI lab, not COHERE HEALTH
    "harvey":  "harvey ai",            # HARVEY AI CORP, not the IT-staffing HARVEY NASH
    "mercury": "mercury technologies", # the fintech, not the insurer/healthcare crowd
}


@dataclass
class VisaIntel:
    """The oracle's verdict on one company. `status` is the headline; the
    numbers tag along so you can talk back to the headline."""
    company: str                      # what you asked about
    status: str                       # no_record | unknown | new_hire_sponsor | renewals_only
    note: str                         # plain-English read, caveats and all
    matched_name: str | None = None   # the legal name we actually landed on
    new_hires: int = 0                # fresh petitions, both years — YOUR signal
    renewals: int = 0                 # continuations, both years — context
    new_2025: int = 0
    new_2026: int = 0                 # half-year (FY2026 thru Q2)
    cont_2025: int = 0
    cont_2026: int = 0                # half-year
    candidates: list | None = None    # the suspect lineup, only when status == unknown


def _connect():
    """A read-only peek into jobs.db, rows dressed up to act like dicts. No
    commit — the oracle looks but never lays a finger on anything."""
    @contextmanager
    def _cm():
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    return _cm()


def _note_for(status: str, m: SponsorMatch | None) -> str:
    """Write the plain-English verdict. Substance stays plain on purpose — this
    is where we're honest about what the numbers do and don't mean, especially
    the FY2026 half-year caveat. No cute here; this is the part you trust."""
    if status == "no_record":
        return ("No H-1B approvals in the FY2025–2026 data. Could genuinely not "
                "sponsor, or files under a legal name we don't match. Absence "
                "isn't a hard 'no' — just no track record to lean on.")
    if status == "unknown":
        return ("Ambiguous — several unrelated employers share this name and none "
                "is clearly the one you mean. Resolve by hand before trusting it.")
    if status == "new_hire_sponsor":
        return (f"Files NEW H-1B petitions: {m.total_new} (2025: {m.new_2025}, "
                f"2026-to-date: {m.new_2026}), plus {m.total_cont} renewals. New "
                f"petitions are the number that speaks to your odds. (2026 is a "
                f"half-year, so its figure is light by construction.)")
    if status == "renewals_only":
        return (f"Only renewals on record ({m.total_cont}), zero new petitions. "
                f"Sponsors existing staff but no fresh hires in this window — a "
                f"thinner signal for someone applying in from outside.")
    return "No read available."


def get_visa_signal(company_name: str) -> VisaIntel:
    """The front door. Company name in, honest sponsorship read out.

    The dance: pin any known alias first, then let find_sponsor do the
    exact/prefix/ambiguous detective work, then translate the match into a
    structural status (new-hire vs renewals-only) with the raw numbers attached."""
    # Pin the curated aliases before matching; otherwise let the name walk through.
    normalized = normalize_name(company_name)
    target = COMPANY_ALIASES.get(normalized, company_name)

    with _connect() as conn:
        m = find_sponsor(conn, target)

    # Not in the book at all.
    if m.match_type == "none":
        return VisaIntel(company=company_name, status="no_record",
                         note=_note_for("no_record", None))

    # A crowd of lookalikes and no alias to break the tie — stay honest, hand 'em back.
    if m.match_type == "ambiguous":
        return VisaIntel(company=company_name, status="unknown",
                         note=_note_for("unknown", None),
                         candidates=m.candidates)

    # A clean exact/prefix hit. Label by STRUCTURE, not size:
    #   any new petitions at all -> new_hire_sponsor (the signal you actually want)
    #   only renewals            -> renewals_only (sponsors, but not fresh blood)
    if m.total_new > 0:
        status = "new_hire_sponsor"
    elif m.total_cont > 0:
        status = "renewals_only"
    else:
        status = "no_record"  # defensive; the loader shouldn't store all-zero rows

    return VisaIntel(
        company=company_name,
        status=status,
        note=_note_for(status, m),
        matched_name=m.raw_name,
        new_hires=m.total_new,
        renewals=m.total_cont,
        new_2025=m.new_2025,
        new_2026=m.new_2026,
        cont_2025=m.cont_2025,
        cont_2026=m.cont_2026,
    )


# ── Standalone demo: parade the whole roster past the oracle ──────────────────
# Lets you eyeball the signal end-to-end before it gets wired into the pipeline.
# Run from the project root:  python -m services.visa_intel
if __name__ == "__main__":
    from config.companies import COMPANIES

    _ICON = {
        "new_hire_sponsor": "🟢",
        "renewals_only":    "🟠",
        "no_record":        "⬜",
        "unknown":          "🟡",
    }

    print("\n🔮 The visa oracle gazes into the roster...\n")
    for display_name, _slug in COMPANIES:
        intel = get_visa_signal(display_name)
        icon = _ICON.get(intel.status, "❔")
        print(f"  {icon} {display_name:<18} {intel.status}")
        if intel.status in ("new_hire_sponsor", "renewals_only"):
            print(f"        → {intel.matched_name}")
            print(f"        → new: {intel.new_hires} (25: {intel.new_2025}, 26: {intel.new_2026})"
                  f"  |  renewals: {intel.renewals} (25: {intel.cont_2025}, 26: {intel.cont_2026})")
        elif intel.status == "unknown":
            print(f"        → {len(intel.candidates)} lookalikes, the jury's still out")
    print()