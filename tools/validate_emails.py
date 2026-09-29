"""Validate a finished city's email file in place (syntax + MX), for cities run before validation existed.

    python3 tools/validate_emails.py london

Rows whose email fails fall back to the first valid address in other_emails, otherwise they are
removed; the websites file drops failed addresses from its email column. Rejects are listed in
output/<city>/<city>_rejected_emails.csv.
"""

import concurrent.futures as cf
import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from londonfood.validate import MXChecker  # noqa: E402

city = sys.argv[1] if len(sys.argv) > 1 else "london"
base = os.path.join(os.path.dirname(__file__), "..", "output", city)
mx = MXChecker(os.path.join(os.path.expanduser("~"), ".foodcontacts-cache", f"{city}_mx.jsonl"))


def load(kind):
    with open(os.path.join(base, f"{city}_food_{kind}.csv"), encoding="utf-8") as f:
        r = csv.DictReader(f)
        return r.fieldnames, list(r)


def save(kind, fields, rows):
    with open(os.path.join(base, f"{city}_food_{kind}.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


ef, erows = load("emails")
wf, wrows = load("websites")
emails = {e.strip() for r in erows for e in [r["email"]] + r.get("other_emails", "").split(";") if e.strip()}
emails |= {r["email"] for r in wrows if r.get("email")}
with cf.ThreadPoolExecutor(32) as ex:
    verdict = dict(zip(emails, ex.map(mx.check, emails)))
bad = {e: v for e, v in verdict.items() if v != "valid"}

kept, rejected, seen = [], [], set()
for r in erows:
    cands = [e.strip() for e in [r["email"]] + r.get("other_emails", "").split(";") if e.strip()]
    good = [e for e in cands if e not in bad]
    rejected += [{"email": e, "reason": bad[e], "name": r["name"], "borough": r.get("borough", "")}
                 for e in cands if e in bad]
    if not good or good[0] in seen:
        continue
    seen.add(good[0])
    kept.append({**r, "email": good[0], "other_emails": "; ".join(good[1:])})
for r in wrows:
    if r.get("email") in bad:
        r["email"] = ""
save("emails", ef, kept)
save("websites", wf, wrows)
with open(os.path.join(base, f"{city}_rejected_emails.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["email", "reason", "name", "borough"])
    w.writeheader()
    w.writerows(rejected)
from collections import Counter  # noqa: E402
print(f"{len(emails)} addresses checked; {len(bad)} failed {dict(Counter(bad.values()))}; "
      f"emails file {len(erows)} -> {len(kept)} rows")
