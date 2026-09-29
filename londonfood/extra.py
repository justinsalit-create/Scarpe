"""Extra website sources for venues with none listed: Wikidata, and chain branches."""

import json
import os
import re
import urllib.parse
from collections import Counter, defaultdict

from . import areas, emails, http
from .discover import slug

WIKIDATA_QUERY = """
SELECT ?item ?itemLabel ?site ?coord WHERE {
  SERVICE wikibase:box { ?item wdt:P625 ?coord .
    bd:serviceParam wikibase:cornerSouthWest "Point(%(west)s %(south)s)"^^geo:wktLiteral .
    bd:serviceParam wikibase:cornerNorthEast "Point(%(east)s %(north)s)"^^geo:wktLiteral . }
  ?item wdt:P31 ?type .
  VALUES ?type { wd:Q11707 wd:Q212198 wd:Q30022 wd:Q274393 wd:Q5307737 wd:Q1076486 wd:Q27686 wd:Q1062979 }
  ?item wdt:P856 ?site .
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}"""


LONDON_BBOX = {"south": 51.28, "west": -0.52, "north": 51.70, "east": 0.34}


def fetch_wikidata(cache_dir, bbox=None):
    path = os.path.join(cache_dir, "wikidata.json")
    if not os.path.exists(path):
        query = WIKIDATA_QUERY % (bbox or LONDON_BBOX)
        url = "https://query.wikidata.org/sparql?format=json&query=" + urllib.parse.quote(query)
        text = http.get(url, headers={"Accept": "application/sparql-results+json"}, timeout=180,
                        max_bytes=100_000_000)[1]
        with open(path, "w") as f:
            json.dump(json.loads(text)["results"]["bindings"], f)
    with open(path) as f:
        return json.load(f)


def _point(wkt):
    m = re.match(r"Point\(([-\d.]+) ([-\d.]+)\)", wkt)
    return (float(m.group(2)), float(m.group(1))) if m else None


def add_wikidata(venues, cache_dir, bbox=None):
    """Official websites from Wikidata for venues with the same name within 200 m."""
    try:
        items = fetch_wikidata(cache_dir, bbox)
    except Exception as e:
        print(f"Wikidata unavailable: {e}", file=__import__("sys").stderr)
        return 0
    by_name = defaultdict(list)
    for it in items:
        pt = _point(it["coord"]["value"])
        if pt:
            by_name[slug(it["itemLabel"]["value"])].append((pt, it["site"]["value"]))
    added = 0
    for v in venues:
        if v["website"] or not v.get("lat"):
            continue
        for pt, site in by_name.get(slug(v["name"]), []):
            if emails.not_venue_site(site):
                continue
            if areas.within(pt, (v["lat"], v["lon"]), 200):
                v["website"], v["website_type"] = emails.normalize_url(site), "own site (Wikidata)"
                added += 1
                break
    return added


def _domain(url):
    return urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")


def add_brand_sites(venues, min_branches=3, agreement=0.8):
    """Chain branches: when >= 3 mapped venues with exactly this name share one website domain (>= 80% of
    them), branches with the same name and no website get that chain's website."""
    sites = defaultdict(list)
    for v in venues:
        if v["website"] and v.get("website_type", "").startswith("own site") and not emails.is_aggregator(v["website"]):
            sites[slug(v["name"])].append(v["website"])
    brands = {}
    for name, urls in sites.items():
        if len(urls) < min_branches or len(name) < 3:
            continue
        dom, n = Counter(_domain(u) for u in urls).most_common(1)[0]
        if n / len(urls) >= agreement:
            brands[name] = (f"https://{dom}/", len(urls))
    added = 0
    for v in venues:
        b = brands.get(slug(v["name"]))
        if b and not v["website"]:
            v["website"], v["website_type"] = b[0], f"chain site (same name as {b[1]} mapped branches)"
            added += 1
    return added
