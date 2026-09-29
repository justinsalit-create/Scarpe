"""Overture Maps places (open data, CDLA-Permissive / ODbL): far denser business coverage than
OpenStreetMap in many cities, with websites, emails, phones and social links.

Read straight from the public GeoParquet release with DuckDB over HTTPS (only the row groups inside
the city's box are downloaded). Places are kept when they are food & drink, inside one of the city's
districts (Overture division areas, e.g. region TH-10 = Bangkok), confident enough, and not marked
closed. Configure with "overture" in cities/<slug>.json.
"""

import json
import os
import re
import subprocess
import sys
import urllib.parse

BUCKET = "https://overturemaps-us-west-2.s3.us-west-2.amazonaws.com/"

# Overture taxonomy leaf -> the OSM-style tags our categoriser understands
LEAF_TAGS = {
    "bar": {"amenity": "bar", "food": "yes"}, "pub": {"amenity": "pub"}, "gastropub": {"amenity": "pub"},
    "cafe": {"amenity": "cafe"}, "coffee_shop": {"amenity": "cafe"}, "bakery": {"shop": "bakery"},
    "food_truck": {"amenity": "fast_food", "street_vendor": "yes"}, "fast_food_restaurant": {"amenity": "fast_food"},
    "ice_cream_shop": {"amenity": "ice_cream"}, "donut_shop": {"cuisine": "donut"}, "bagel_shop": {"cuisine": "bagel"},
    "deli": {"shop": "deli"}, "steakhouse": {"cuisine": "steak_house"}, "breakfast_and_brunch_restaurant":
    {"cuisine": "breakfast"}, "barbecue_restaurant": {"cuisine": "bbq"}, "sushi_restaurant": {"cuisine": "sushi"},
    "seafood_restaurant": {"cuisine": "seafood"}, "halal_restaurant": {"diet:halal": "yes"},
}


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout


def latest_release():
    x = _run(["curl", "-sS", "-m", "30", BUCKET + "?list-type=2&prefix=release/&delimiter=/"])
    return sorted(re.findall(r"<Prefix>release/([^<]+)/</Prefix>", x))[-1]


def files(release, theme, typ):
    x = _run(["curl", "-sS", "-m", "30", BUCKET + f"?list-type=2&prefix=release/{release}/theme={theme}/type={typ}/"])
    return [BUCKET + k for k in re.findall(r"<Key>([^<]+)</Key>", x)]


def connect():
    import duckdb
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial;")
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        p = urllib.parse.urlparse(proxy)
        con.execute(f"SET http_proxy='{p.hostname}:{p.port}'")
    if os.path.exists("/root/.ccr/ca-bundle.crt"):
        con.execute("SET ca_cert_file='/root/.ccr/ca-bundle.crt'")
    return con


def fetch_places(cfg, cache_dir):
    """Food & drink places in the city's districts, as plain dicts (cached as JSON)."""
    path = os.path.join(cache_dir, "overture_places.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    ov, b = cfg["overture"], cfg["bbox"]
    release = ov.get("release") or latest_release()
    con = connect()
    places, divisions = files(release, "places", "place"), files(release, "divisions", "division_area")
    regions = ", ".join(f"'{r}'" for r in ov["regions"])
    box = f"bbox.xmax >= {b['west']} AND bbox.xmin <= {b['east']} AND bbox.ymax >= {b['south']} AND bbox.ymin <= {b['north']}"
    sql = f"""
    WITH d AS (
      SELECT coalesce(names.common['en'], names.primary) AS district, region, geometry
      FROM read_parquet({divisions!r})
      WHERE subtype = '{ov.get("division_subtype", "county")}' AND region IN ({regions}) AND {box}),
    p AS (
      SELECT id, names.primary AS name, names.common['en'] AS name_en, taxonomy.hierarchy AS hierarchy,
             websites, emails, socials, phones, addresses[1].freeform AS address, addresses[1].postcode AS postcode,
             confidence, operating_status, geometry, sources[1].dataset AS dataset
      FROM read_parquet({places!r})
      WHERE {box} AND taxonomy.hierarchy[1] = 'food_and_drink'
        AND confidence >= {ov.get("min_confidence", 0.5)}
        AND coalesce(operating_status, 'open') = 'open')
    SELECT p.* EXCLUDE (geometry), ST_Y(p.geometry) AS lat, ST_X(p.geometry) AS lon, d.district, d.region
    FROM p JOIN d ON ST_Contains(d.geometry, p.geometry)
    """
    cols = None
    rows = []
    cur = con.execute(sql)
    cols = [c[0] for c in cur.description]
    for r in cur.fetchall():
        rows.append({c: (list(v) if isinstance(v, tuple) else v) for c, v in zip(cols, r)})
    with open(path, "w") as f:
        json.dump({"release": release, "places": rows}, f)
    return {"release": release, "places": rows}


def to_venue(p):
    """Overture place -> the same venue dict shape as osm.to_venue."""
    leaf = (p.get("hierarchy") or ["food_and_drink"])[-1]
    tags = dict(LEAF_TAGS.get(leaf, {}))
    if leaf.endswith("_restaurant") and leaf not in LEAF_TAGS:
        tags["cuisine"] = leaf[: -len("_restaurant")]
    tags.setdefault("amenity", "restaurant" if "restaurant" in (p.get("hierarchy") or []) else "cafe"
                    if "casual_eatery" in (p.get("hierarchy") or []) else "restaurant")
    socials = p.get("socials") or []
    sites = p.get("websites") or []
    return {
        "borough": p.get("district") or "",
        "name": (p.get("name") or "").strip(),
        "name_en": (p.get("name_en") or "").strip(),
        "tags": {**tags, "name": p.get("name") or ""},
        "email": "; ".join(p.get("emails") or []),
        "website": sites[0] if sites else "",
        "phone": (p.get("phones") or [""])[0] or "",
        "address": p.get("address") or "",
        "postcode": p.get("postcode") or "",
        "facebook": next((s for s in socials if "facebook" in s.lower()), ""),
        "instagram": next((s for s in socials if "instagram" in s.lower()), ""),
        "osm_url": f"overture:{p['id']}",
        "lat": p.get("lat"),
        "lon": p.get("lon"),
        "source": "overture" + (f" ({p['dataset']})" if p.get("dataset") else ""),
    }


def district_map(osm_units, overture_names):
    """Map Overture district names onto the OSM district labels ('Bang Rak' -> 'Bang Rak District')."""
    from .names import fold
    by_fold = {fold(u.replace(" District", "")): u for u in osm_units}
    return {n: by_fold.get(fold(n), n if n.endswith("District") else f"{n} District") for n in overture_names}


def log(msg):
    print(msg, file=sys.stderr)
