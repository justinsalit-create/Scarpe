"""Tag venues with their neighbourhood from data/london_areas.csv (Borough, Area, Post town, Postcode districts).

Each area is located via the matching OpenStreetMap place node (suburb / neighbourhood / village ...).
A venue gets the nearest area in its borough, preferring areas whose postcode districts include the
venue's postcode district; when no area has coordinates it falls back to the postcode district alone.
"""

import csv
import json
import math
import os
import re
import urllib.parse
from collections import Counter

from . import http, osm

AREAS_CSV = os.path.join(os.path.dirname(__file__), "..", "data", "london_areas.csv")

PLACES_QUERY = """
[out:json][timeout:300];
area["name"="Greater London"]["boundary"="administrative"]->.a;
(
  node["place"~"^(suburb|neighbourhood|quarter|village|town|hamlet|locality)$"](area.a);
  node["place"]["name"="Hamsey Green"];
);
out;
"""


def clean_name(name):
    """'Barnet (also Chipping Barnet, High Barnet)' -> ['Barnet', 'Chipping Barnet', 'High Barnet'];
    'Burroughs, The' -> ['The Burroughs']."""
    m = re.match(r"^(.*?)\s*\(also (.*)\)\s*$", name)
    names = [m.group(1)] + [x.strip() for x in m.group(2).split(",")] if m else [name]
    return [re.sub(r"^(.*), The$", r"The \1", n).strip() for n in names]


def district(postcode):
    """'E1 6AN' -> 'E1'; 'EC1A 1BB' -> 'EC1' (the spreadsheet uses EC1 / WC2 without the letter)."""
    pc = postcode.upper().strip()
    out = pc.split()[0] if " " in pc else pc[:-3] if len(pc) > 4 else pc
    return re.sub(r"^((?:EC|WC|W|SW|SE|NW|N|E)\d+)[A-Z]$", r"\1", out)


def districts(postcode):
    """Both spellings of the outward code, e.g. {'E1W', 'E1'} or {'EC1A', 'EC1'}."""
    if not postcode.strip():
        return set()
    pc = postcode.upper().strip()
    raw = pc.split()[0] if " " in pc else pc[:-3] if len(pc) > 4 else pc
    return {raw, district(postcode)}


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower().replace("st.", "st").replace("saint ", "st "))


def load_areas(path=AREAS_CSV):
    with open(path, newline="") as f:
        rows = list(csv.reader(f))[1:]
    return [{"borough": b, "area": a, "names": clean_name(a), "post_town": t,
             "districts": {d.strip().upper() for d in (ds or "").split(",") if d.strip()}}
            for b, a, t, ds in rows if b and a]


def fetch_places(cache_dir):
    path = os.path.join(cache_dir, "osm", "places.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    body = urllib.parse.urlencode({"data": PLACES_QUERY}).encode()
    last = None
    for url in osm.OVERPASS_URLS * 2:
        try:
            elements = json.loads(http.get(url, timeout=360, data=body, max_bytes=200_000_000)[1])["elements"]
            break
        except Exception as e:
            last = e
    else:
        raise RuntimeError(f"Overpass place lookup failed: {last}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(elements, f)
    return elements


def _dist(a, b):
    dx = (a[1] - b[1]) * math.cos(math.radians(a[0]))
    return math.hypot(a[0] - b[0], dx)


def within(center, pt, metres):
    return _dist(center, pt) * 111_320 <= metres


def locate(areas, places, venues):
    """Give each area a (lat, lon) from the OSM place node of the same name nearest its borough's venues."""
    centroids = {}
    for b in {a["borough"] for a in areas}:
        pts = [(v["lat"], v["lon"]) for v in venues if v["borough"] == b and v["lat"] and v["lon"]]
        if pts:
            centroids[b] = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
    by_name = {}
    for p in places:
        name = p.get("tags", {}).get("name")
        if name:
            by_name.setdefault(norm(name), []).append((p["lat"], p["lon"]))
    for a in areas:
        cands = [pt for n in a["names"] for pt in by_name.get(norm(n), [])]
        c = centroids.get(a["borough"])
        a["point"] = min(cands, key=lambda pt: _dist(pt, c)) if cands and c else (cands[0] if cands else None)
    return areas


def assign(venues, areas):
    """Set v['area'] for every venue."""
    by_borough = {}
    for a in areas:
        by_borough.setdefault(a["borough"], []).append(a)
    for v in venues:
        cands = by_borough.get(v["borough"], [])
        ds = districts(v.get("postcode") or "")
        in_district = [a for a in cands if ds & a["districts"]]
        pool = in_district or cands
        located = [a for a in pool if a["point"]]
        if v.get("lat") and v.get("lon") and located:
            v["area"] = min(located, key=lambda a: _dist(a["point"], (v["lat"], v["lon"])))["area"]
        elif in_district:
            v["area"] = " / ".join(sorted(a["area"] for a in in_district))
        else:
            v["area"] = ""


def summary(email_rows, site_rows, areas):
    """Rows of (borough, area, venues with email, venues with website) for every spreadsheet area."""
    e = Counter((r["borough"], r.get("area", "")) for r in email_rows)
    w = Counter((r["borough"], r.get("area", "")) for r in site_rows)
    rows = [{"borough": a["borough"], "area": a["area"], "emails": e[(a["borough"], a["area"])],
             "websites": w[(a["borough"], a["area"])]} for a in areas]
    known = {(a["borough"], a["area"]) for a in areas}
    for key in sorted(set(e) | set(w)):
        if key not in known:  # e.g. venues with no area match
            rows.append({"borough": key[0], "area": key[1] or "(unassigned)", "emails": e[key], "websites": w[key]})
    return rows
