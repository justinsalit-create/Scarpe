"""Food Standards Agency hygiene-rating register: every registered food business per London borough.

Open Government Licence data (https://api.ratings.food.gov.uk). It has no emails or websites, but it
lists far more venues than OpenStreetMap, including mobile caterers (food trucks).
"""

import json
import os
import re

from . import http
from .categories import NAME_HINTS

API = "https://api.ratings.food.gov.uk"
HEADERS = {"x-api-version": "2", "Accept": "application/json"}

# FSA business type -> the OSM-style tags our categoriser understands
BUSINESS_TYPES = {
    1: {"amenity": "restaurant"},        # Restaurant/Cafe/Canteen
    7844: {"amenity": "fast_food"},      # Takeaway/sandwich shop
    7843: {"amenity": "pub"},            # Pub/bar/nightclub
    7846: {"amenity": "fast_food", "street_vendor": "yes"},  # Mobile caterer
    4613: {},                            # Retailers - other: only bakeries, delis, ice cream etc.
}
RETAIL_KEEP = ("Bakery", "Deli", "Ice cream", "Donuts", "Bagels")

# FSA authority names that differ from ours
AUTHORITY_ALIASES = {"City of London Corporation": "City of London",
                     "Kingston-Upon-Thames": "Kingston upon Thames",
                     "Richmond-Upon-Thames": "Richmond upon Thames"}


def _get_json(path):
    return json.loads(http.get(API + path, headers=HEADERS, timeout=60, max_bytes=200_000_000)[1])


def authority_ids(cache_dir):
    path = os.path.join(cache_dir, "fsa", "authorities.json")
    if not os.path.exists(path):
        auths = [a for a in _get_json("/Authorities")["authorities"] if a["RegionName"] == "London"]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({AUTHORITY_ALIASES.get(a["Name"], a["Name"]): a["LocalAuthorityId"] for a in auths}, f)
    with open(path) as f:
        return json.load(f)


def fetch_authority(name, cache_dir):
    """Establishments for an authority outside London (e.g. Tandridge), looked up by name."""
    auths = [a for a in _get_json("/Authorities")["authorities"] if a["Name"] == name]
    return fetch_borough(name, cache_dir, {name: auths[0]["LocalAuthorityId"]}) if auths else []


def fetch_borough(short_name, cache_dir, ids=None):
    """All relevant FSA establishments for a borough (cached)."""
    ids = ids or authority_ids(cache_dir)
    if short_name not in ids:
        return []
    path = os.path.join(cache_dir, "fsa", short_name.replace(" ", "_") + ".json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    out = []
    for type_id in BUSINESS_TYPES:
        page = 1
        while True:
            data = _get_json(f"/Establishments?localAuthorityId={ids[short_name]}&businessTypeId={type_id}"
                             f"&pageSize=5000&pageNumber={page}")
            out += data["establishments"]
            if page >= data["meta"]["totalPages"]:
                break
            page += 1
    with open(path, "w") as f:
        json.dump(out, f)
    return out


def to_venue(est, borough):
    """Flatten an FSA establishment into the same venue dict shape as osm.to_venue (or None to skip)."""
    name = (est.get("BusinessName") or "").strip()
    tags = dict(BUSINESS_TYPES.get(est.get("BusinessTypeID"), {}), name=name)
    if est.get("BusinessTypeID") == 4613:
        if not any(NAME_HINTS[c].search(name) for c in RETAIL_KEEP):
            return None
        tags["shop"] = "bakery" if NAME_HINTS["Bakery"].search(name) else "deli"
    geo = est.get("geocode") or {}
    try:
        lat, lon = float(geo.get("latitude")), float(geo.get("longitude"))
    except (TypeError, ValueError):
        lat = lon = None
    addr = ", ".join(x for x in (est.get(f"AddressLine{i}") for i in range(1, 4)) if x)
    return {
        "borough": borough, "name": name, "tags": tags, "email": "", "website": "",
        "phone": est.get("Phone", ""), "address": addr, "postcode": est.get("PostCode", "") or "",
        "facebook": "", "instagram": "",
        "osm_url": f"https://ratings.food.gov.uk/business/{est['FHRSID']}", "lat": lat, "lon": lon,
        "source": "food standards agency",
    }


_STOP = re.compile(r"\b(the|ltd|limited|restaurant|cafe|café|bar|kitchen|london|and|&)\b")


def match_key(name, postcode):
    """Loose identity used to spot an FSA venue that OSM already has."""
    n = re.sub(r"[^a-z0-9]", "", _STOP.sub("", name.lower()))
    return n[:12], re.sub(r"\s", "", postcode.upper())
