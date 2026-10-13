"""Merge a partner pipeline's venue list (e.g. Muse's social-sourced CSV) into a city's venues.

Config ("partner_csvs" in cities/<slug>.json), one entry per file:
  {"path": "data/partner/<file>.csv", "source": "muse (social)",
   "district_map": {"Pattaya": "Pattaya (area not given)", "Jomtien": "Jomtien"},
   "exclude_districts": ["Chon Buri"], "admin_district": "Bang Lamung"}
Expected columns: name, district_label, instagram_handle, instagram_url, facebook_page, facebook_url,
website, email, phone, source_method, notes.

Rules (agreed with the partner): rows flagged "NOT A FOOD VENUE" or "permanently closed" are dropped;
a partner row is the same venue as one of ours when an email / Instagram / Facebook matches, or when
the names match (names.py) AND a website or phone matches. Partner rows have no location, so a
full, distinctive name (6+ letters folded) held by exactly ONE of our venues also counts; a name
several of ours share (a chain) is kept and listed in the report instead. A matched venue gets the partner's missing contacts.
"""

import csv
import os
import re
import sys

from . import emails, names, social

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NOT_FOOD = re.compile(r"not a food venue", re.I)
PERM_CLOSED = re.compile(r"permanently closed", re.I)
TEMP_CLOSED = re.compile(r"temporarily closed", re.I)


def _phones(s, cc):
    return [p for p in (names.phone_key(x, cc) for x in re.split(r"[;,/]", s or "")) if p]


def _keys(v, cc):
    """Contact keys for matching: ('strong', ...) identify a venue alone; ('weak', ...) need the name too."""
    out = []
    for e in re.split(r"[;,\s]+", (v.get("email") or "").lower()):
        if "@" in e:
            out.append(("strong", "e:" + e))
    if v.get("instagram"):
        out.append(("strong", "i:" + v["instagram"].lower()))
    if v.get("facebook"):
        out.append(("strong", "f:" + v["facebook"].lower()))
    if v.get("website") and not emails.not_venue_site(v["website"]):
        out.append(("weak", "w:" + names.site_key(v["website"])))
    for p in _phones(v.get("phone"), v.get("_cc", "")):
        out.append(("weak", "p:" + p))
    return out


def _fill(match, p):
    """Give our venue the partner's missing contacts; a different partner email is kept as an extra address."""
    for fld in ("email", "website", "phone", "facebook", "instagram"):
        if not match.get(fld) and p.get(fld):
            match[fld] = p[fld]
    ours = {e.strip().lower() for e in re.split(r"[;,\s]+", match.get("email") or "") if "@" in e}
    extra = [e.strip() for e in re.split(r"[;,\s]+", p.get("email") or "") if "@" in e and e.strip().lower() not in ours]
    if extra:
        match["email"] = "; ".join([match["email"]] + extra)


def merge(cfg, venues, categorize, local):
    cc = cfg.get("country_code", "")
    for v in venues:  # canonical social links on our side too (incl. a Facebook page given as website)
        v["_cc"] = cc
        if v.get("website") and emails.not_venue_site(emails.normalize_url(v["website"])):
            v.setdefault("social_website", emails.normalize_url(v["website"]))
        social.collect(v)
    index = {}
    for v in venues:
        for strength, k in _keys(v, cc):
            index.setdefault(k, []).append(v)
    by_name = {}
    for v in venues:
        f = names.fold(v.get("name")).replace(" ", "")
        if len(f) >= 6:
            by_name.setdefault(f, []).append(v)

    report = {"rows": 0, "excluded": {}, "merged": 0, "new": 0, "name_only_lookalikes": []}
    for spec in cfg.get("partner_csvs", []):
        with open(os.path.join(ROOT, spec["path"]), encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        for i, r in enumerate(rows):
            report["rows"] += 1
            notes = r.get("notes", "")
            why = ("not a food venue" if NOT_FOOD.search(notes) else "permanently closed" if PERM_CLOSED.search(notes)
                   else "outside the area" if r.get("district_label") in spec.get("exclude_districts", []) else "")
            if why:
                report["excluded"].setdefault(why, []).append(r["name"])
                continue
            site = emails.normalize_url(r.get("website", ""))
            p = {
                "name": r["name"].strip(), "name_en": "", "tags": {"name": r["name"].strip()},
                "email": (r.get("email") or "").strip(),
                "website": "" if (not site or emails.not_venue_site(site)) else site,
                "social_website": site if site and emails.not_venue_site(site) else "",
                "phone": (r.get("phone") or "").split(";")[0].strip(),
                "address": "", "postcode": "",
                "facebook": r.get("facebook_url") or r.get("facebook_page") or "",
                "instagram": r.get("instagram_url") or r.get("instagram_handle") or "",
                "osm_url": f"{spec.get('source', 'partner')}:{os.path.basename(spec['path'])}#{i + 2}",
                "lat": None, "lon": None, "source": spec.get("source", "partner"), "_cc": cc,
                "partner_notes": ("temporarily closed (per partner); " if TEMP_CLOSED.search(notes) else "") + notes,
            }
            social.collect(p)
            pkeys = _keys(p, cc)
            match = None
            for strength, k in pkeys:
                for v in index.get(k, []):
                    if strength == "strong" or names.names_match(v.get("name"), p["name"]):
                        match = v
                        break
                if match:
                    break
            if match:
                _fill(match, p)
                if p["social_website"] and not match.get("social_website"):
                    match["social_website"] = p["social_website"]
                match["source"] = (match.get("source") or "") + " + " + p["source"]
                match["partner_notes"] = p["partner_notes"]
                social.collect(match)
                report["merged"] += 1
                continue
            look = by_name.get(names.fold(p["name"]).replace(" ", ""), [])
            if len(look) == 1:
                # Partner rows carry no location, so a full, distinctive name (6+ letters after folding) that
                # exactly one of our venues in this small area has is taken as the same venue.
                match = look[0]
                _fill(match, p)
                if p["social_website"] and not match.get("social_website"):
                    match["social_website"] = p["social_website"]
                match["source"] = (match.get("source") or "") + " + " + p["source"]
                match["partner_notes"] = p["partner_notes"]
                social.collect(match)
                report["merged_by_unique_name"] = report.get("merged_by_unique_name", 0) + 1
                continue
            if look:  # same name as several of ours (e.g. a chain's branches): keep, but list for review
                report["name_only_lookalikes"].append((p["name"], look[0].get("name"), len(look)))
            label = spec.get("district_map", {}).get(r.get("district_label"), r.get("district_label") or "")
            p.update(borough=label, area=label, admin_district=spec.get("admin_district", ""),
                     name_local=p["name"] if local.search(p["name"]) else "", latin_name=not local.search(p["name"]))
            p["categories"] = categorize({"amenity": "restaurant", "name": p["name"]})
            venues.append(p)
            for strength, k in pkeys:
                index.setdefault(k, []).append(p)
            report["new"] += 1
    ex = {k: len(v) for k, v in report["excluded"].items()}
    print(f"Partner rows: {report['rows']}; excluded {ex}; merged into existing venues {report['merged']} "
          f"by contact + {report.get('merged_by_unique_name', 0)} by unique full name; "
          f"new venues {report['new']}; name-only look-alikes kept {len(report['name_only_lookalikes'])}",
          file=sys.stderr)
    return report
