"""Generate the two divergent CRM CSVs the whole demo lives on.

Fully deterministic: rows are hand-authored literals (no RNG), so the demo never
varies between runs. Writes `seed/crm_a.csv` and `seed/crm_b.csv`.

Planted structure (exercised by the acceptance tests across all five units):

  * Colin Marsh  — shared email across A/B  -> AUTO-band resolution.
                   title ("VP Engineering") vs job_role ("Director, Platform"),
                   both dated -> conflict_ordered.
  * Dana Osei    — NO shared email (B email blank) -> middle-band (LLM) resolution
                   on name + org. One title undated (B last_touch blank)
                   -> conflict_unordered.
  * "Colin Marsh-Jones" — near-miss distractor at a DIFFERENT org; must NOT resolve
                   to Colin Marsh.
  * `region` populated only in crm_b -> no ontology node -> quarantine + propose (Unit 2).

Field-to-node truth (divergent names, same meaning):
  full_name/name -> person, email/primary_email -> email,
  title/job_role -> role, company/org_name -> organisation.
  updated_at/last_touch order conflicts (not served as attributes).
"""
from __future__ import annotations
import csv
from pathlib import Path

SEED_DIR = Path(__file__).resolve().parent

# crm_a.csv: id, full_name, email, title, company, updated_at
CRM_A_HEADER = ["id", "full_name", "email", "title", "company", "updated_at"]
CRM_A_ROWS = [
    # --- planted identities ---
    ["a1", "Colin Marsh", "colin.marsh@stripe.com", "VP Engineering", "Stripe", "2025-11-02"],
    ["a2", "Dana Osei", "dana.osei@acme.io", "Head of Ops", "Acme", "2025-10-01"],
    # --- distractors ---
    ["a3", "Priya Nair", "priya.nair@stripe.com", "Staff Engineer", "Stripe", "2025-08-15"],
    ["a4", "Marcus Webb", "marcus.webb@globex.com", "CFO", "Globex", "2025-07-22"],
    ["a5", "Elena Rossi", "elena.rossi@acme.io", "Account Executive", "Acme", "2025-09-03"],
    ["a6", "Tomas Vega", "tomas.vega@initech.com", "Product Manager", "Initech", "2025-06-30"],
    ["a7", "Sarah Chen", "sarah.chen@umbrella.co", "Head of Design", "Umbrella", "2025-10-18"],
    ["a8", "Colin Marsh-Jones", "colin.mjones@hooli.com", "VP Sales", "Hooli", "2025-05-11"],
    ["a9", "Ravi Patel", "ravi.patel@globex.com", "Data Scientist", "Globex", "2025-09-27"],
]

# crm_b.csv: contact_id, name, primary_email, job_role, org_name, last_touch, region
CRM_B_HEADER = ["contact_id", "name", "primary_email", "job_role", "org_name", "last_touch", "region"]
CRM_B_ROWS = [
    # --- planted identities ---
    ["b1", "C. Marsh", "colin.marsh@stripe.com", "Director, Platform", "Stripe", "2025-09-10", "EMEA"],
    ["b2", "D. Osei", "", "Operations Lead", "Acme", "", "NA"],
    # --- distractors ---
    ["b3", "Priya Nair", "priya.nair@stripe.com", "Staff Software Engineer", "Stripe", "2025-08-20", "APAC"],
    ["b4", "M. Webb", "marcus.webb@globex.com", "Chief Financial Officer", "Globex", "2025-07-25", "NA"],
    ["b5", "Elena Rossi", "elena.r@acme.io", "Senior AE", "Acme", "2025-09-05", "EMEA"],
    ["b6", "Nina Kowalski", "nina.kowalski@initech.com", "Engineering Manager", "Initech", "2025-08-01", "EMEA"],
    ["b7", "Sarah Chen", "sarah.chen@umbrella.co", "Design Director", "Umbrella", "2025-10-20", "NA"],
    ["b8", "Colin Marsh-Jones", "colin.mjones@hooli.com", "VP of Sales", "Hooli", "2025-05-14", "NA"],
    ["b9", "James O'Brien", "james.obrien@initech.com", "Solutions Architect", "Initech", "2025-09-30", "EMEA"],
]


def _write(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def main() -> None:
    _write(SEED_DIR / "crm_a.csv", CRM_A_HEADER, CRM_A_ROWS)
    _write(SEED_DIR / "crm_b.csv", CRM_B_HEADER, CRM_B_ROWS)
    print(f"wrote {SEED_DIR / 'crm_a.csv'} ({len(CRM_A_ROWS)} rows)")
    print(f"wrote {SEED_DIR / 'crm_b.csv'} ({len(CRM_B_ROWS)} rows)")


if __name__ == "__main__":
    main()
