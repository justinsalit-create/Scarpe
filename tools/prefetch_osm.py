"""Download a city's district venue data from Overpass in parallel into the cache (resumable).

    python3 tools/prefetch_osm.py bangkok [parallel=4]

The main pipeline (python -m londonfood.city) then reads the cached files instead of waiting for the
map server district by district.
"""

import concurrent.futures as cf
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from londonfood import city, osm  # noqa: E402

slug = sys.argv[1]
par = int(sys.argv[2]) if len(sys.argv) > 2 else 4
cfg = city.load_config(slug)
cache = os.path.join(os.path.expanduser("~"), ".foodcontacts-cache", slug)
units = city.fetch_units(cfg, cache)


def get(u):
    label, rel = u
    try:
        n = len(osm.fetch_area(f"{slug}_{label}", 3600000000 + rel, cache))
        return f"{label}: {n}"
    except Exception as e:
        return f"{label}: FAILED {e}"


todo = [u for u in units if not os.path.exists(os.path.join(cache, "osm", f"{slug}_{u[0]}".replace(" ", "_") + ".json"))]
print(f"{len(units) - len(todo)} cached, {len(todo)} to download, {par} at a time", flush=True)
with cf.ThreadPoolExecutor(par) as ex:
    for i, r in enumerate(ex.map(get, todo), 1):
        print(f"[{i}/{len(todo)}] {r}", flush=True)
print("PREFETCH DONE", flush=True)
