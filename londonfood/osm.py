"""Pull every food venue in each London borough from OpenStreetMap (Overpass API)."""

import json
import os
import time
import urllib.parse

from . import http

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

QUERY = """
[out:json][timeout:300];
area["boundary"="administrative"]["name"="{borough}"]->.b;
(
  nwr["amenity"~"^(restaurant|fast_food|cafe|pub|ice_cream|food_court|biergarten|food_truck)$"](area.b);
  nwr["amenity"="bar"]["food"="yes"](area.b);
  nwr["shop"~"^(bakery|deli|pastry|ice_cream)$"](area.b);
  nwr["street_vendor"="yes"]["cuisine"](area.b);
);
out center tags;
"""


def fetch_borough(borough, cache_dir):
    """Return the list of OSM elements for a borough, cached as JSON on disk."""
    path = os.path.join(cache_dir, "osm", borough.replace(" ", "_") + ".json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    body = urllib.parse.urlencode({"data": QUERY.format(borough=borough)}).encode()
    last_err = None
    for attempt in range(6):
        url = OVERPASS_URLS[attempt % len(OVERPASS_URLS)]
        try:
            _, text = http.get(url, timeout=360, data=body, max_bytes=500_000_000)
            elements = json.loads(text)["elements"]
            break
        except Exception as e:  # rate limited / timeout: back off and try the mirror
            last_err = e
            time.sleep(10 * (attempt + 1))
    else:
        raise RuntimeError(f"Overpass failed for {borough}: {last_err}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(elements, f)
    return elements


def to_venue(el, borough):
    """Flatten an OSM element into a venue dict."""
    t = el.get("tags", {})
    lat = el.get("lat") or el.get("center", {}).get("lat")
    lon = el.get("lon") or el.get("center", {}).get("lon")
    addr = " ".join(filter(None, [t.get("addr:housenumber"), t.get("addr:street")]))
    return {
        "borough": borough,
        "name": t.get("name", "").strip(),
        "tags": t,
        "email": (t.get("email") or t.get("contact:email") or "").strip(),
        "website": (t.get("website") or t.get("contact:website") or t.get("url") or "").strip(),
        "phone": (t.get("phone") or t.get("contact:phone") or "").strip(),
        "address": addr,
        "postcode": t.get("addr:postcode", ""),
        "facebook": t.get("contact:facebook", ""),
        "instagram": t.get("contact:instagram", ""),
        "osm_url": f"https://www.openstreetmap.org/{el['type']}/{el['id']}",
        "lat": lat,
        "lon": lon,
    }
