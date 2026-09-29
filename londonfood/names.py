"""Venue name normalisation and same-venue matching (shared with the partner pipeline's master list).

Rules agreed for merging: curly quotes become straight quotes before ASCII folding; names of 4+
characters match when one contains the other; and a match always needs a second field to agree
(email, website, phone or location), never the name alone.
"""

import math
import re
import unicodedata

QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "´": "'", "`": "'"})


def fold(name):
    """"Hon’s BBQ" -> "hons bbq": curly quotes mapped first, then ASCII folding, lowercase, punctuation out."""
    s = unicodedata.normalize("NFKD", (name or "").translate(QUOTES)).encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " and ").replace("'", "")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()


def names_match(a, b):
    fa, fb = fold(a).replace(" ", ""), fold(b).replace(" ", "")
    if not fa or not fb:
        return (a or "").strip() == (b or "").strip() and bool(a)
    if fa == fb:
        return True
    short, long_ = sorted((fa, fb), key=len)
    return len(short) >= 4 and short in long_


def phone_key(phone, country_code=""):
    d = re.sub(r"\D", "", phone or "")
    if country_code and d.startswith(country_code):
        d = "0" + d[len(country_code):]
    return d if len(d) >= 8 else ""


def site_key(url):
    return re.sub(r"^https?://(www\.)?", "", (url or "").lower().strip()).split("#")[0].split("?")[0].rstrip("/")


def near(a, b, metres=100):
    if not (a.get("lat") and b.get("lat")):
        return False
    dy = (a["lat"] - b["lat"]) * 111_320
    dx = (a["lon"] - b["lon"]) * 111_320 * math.cos(math.radians(a["lat"]))
    return math.hypot(dx, dy) <= metres


def same_venue(a, b, country_code=""):
    """Same email / website / phone, or matching names at (nearly) the same place."""
    if a.get("email") and a["email"].lower() == (b.get("email") or "").lower():
        return True
    if a.get("website") and site_key(a["website"]) == site_key(b.get("website")):
        return True
    pa, pb = phone_key(a.get("phone"), country_code), phone_key(b.get("phone"), country_code)
    if pa and pa == pb:
        return True
    return names_match(a.get("name"), b.get("name")) and near(a, b)


def dedupe_venues(venues, country_code=""):
    """Merge duplicates (keeping the first, filling blanks from the others). Returns the kept list."""
    kept, by_key = [], {}

    def keys(v):
        out = []
        if v.get("email"):
            out.append("e:" + v["email"].lower())
        if v.get("website"):
            out.append("w:" + site_key(v["website"]))
        p = phone_key(v.get("phone"), country_code)
        if p:
            out.append("p:" + p)
        if v.get("lat"):
            out.append(f"g:{round(v['lat'], 3)}:{round(v['lon'], 3)}")
        return out

    for v in venues:
        match = None
        for k in keys(v):
            for cand in by_key.get(k, []):
                if same_venue(v, cand, country_code):
                    match = cand
                    break
            if match:
                break
        if match:
            for f in ("email", "website", "phone"):
                if not match.get(f) and v.get(f):
                    match[f] = v[f]
            match["merged"] = match.get("merged", 1) + 1
            continue
        kept.append(v)
        for k in keys(v):
            by_key.setdefault(k, []).append(v)
    return kept
