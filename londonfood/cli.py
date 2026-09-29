"""Build a CSV of London food venues with an email address (or website as a fallback).

    python -m londonfood                       # all 33 boroughs, all categories
    python -m londonfood --boroughs Camden Hackney --categories Pizza Sushi
"""

import argparse
import concurrent.futures as cf
import csv
import json
import os
import re
import sys
import threading
import urllib.parse
from collections import Counter

from . import areas, discover, emails, extra, fsa, osm, places, social, validate
from .boroughs import BOROUGHS, short_name
from .categories import CATEGORIES, categorize

CLOSED_STATUSES = ("closed", "parked", "dead")


class CrawlCache:
    """Per-website crawl results persisted as JSON lines so interrupted runs resume."""

    def __init__(self, path):
        self.path, self.lock, self.data = path, threading.Lock(), {}
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except ValueError:  # last line cut short by a crash / container restart
                        continue
                    # older cache formats, and "unknown" results from before DNS / 403 handling: re-crawl
                    # re-crawl sites where the deeper v3 crawl could still find an email
                    if "emails" in rec and (rec.get("v", 1) >= 3 or rec["status"] not in ("unknown", "ok")
                                            or (rec["status"] == "ok" and rec["emails"])):
                        self.data[rec["key"]] = rec

    @staticmethod
    def key(url):
        p = urllib.parse.urlparse(emails.normalize_url(url))
        return (p.netloc.lower().removeprefix("www.") + p.path.rstrip("/")).lower()

    def get(self, url):
        return self.data.get(self.key(url))

    def put(self, url, emails_found, status, final_url):
        rec = {"key": self.key(url), "emails": emails_found, "status": status, "final_url": final_url, "v": 3}
        with self.lock:
            self.data[rec["key"]] = rec
            with open(self.path, "a") as f:
                f.write(json.dumps(rec) + "\n")


def resolve_boroughs(names):
    if not names:
        return BOROUGHS
    picked = []
    for n in names:
        match = [b for b in BOROUGHS if n.lower() in b.lower()]
        if not match:
            sys.exit(f"Unknown borough: {n}. Choose from: {', '.join(short_name(b) for b in BOROUGHS)}")
        picked += [b for b in match if b not in picked]
    return picked


# Areas from data/london_areas.csv that lie outside the 33 borough boundaries: searched around a point.
EXTRA_AREAS = [{"area": "Hamsey Green", "borough": "Tandridge", "fsa_authority": "Tandridge", "radius": 1200}]


def collect(boroughs, cache_dir, wanted, use_fsa=True, extras=(), places=()):
    """Venues per borough from OSM (+ FSA register), plus any extra areas outside the boroughs."""
    units = [(short_name(b), lambda b=b: osm.fetch_borough(b, cache_dir),
              lambda b=b: fsa.fetch_borough(short_name(b), cache_dir), None) for b in boroughs]
    for x in extras:
        pt = next(((p["lat"], p["lon"]) for p in places if p.get("tags", {}).get("name") == x["area"]), None)
        if not pt:
            print(f"  could not locate extra area {x['area']}", file=sys.stderr)
            continue
        units.append((x["borough"], lambda x=x, pt=pt: osm.fetch_around(x["area"], *pt, x["radius"], cache_dir),
                      lambda x=x: fsa.fetch_authority(x["fsa_authority"], cache_dir), (pt, x["radius"])))

    venues, seen = [], set()
    for i, (label, get_osm, get_fsa, circle) in enumerate(units, 1):
        kept = closed = 0
        candidates = []
        for el in get_osm():
            v = osm.to_venue(el, label)
            if not v["name"]:
                continue
            if osm.is_closed(v["tags"]):
                closed += 1
                continue
            v["source"] = "openstreetmap"
            candidates.append(v)
        fsa_added = 0
        if use_fsa:
            known = {fsa.match_key(v["name"], v["postcode"]) for v in candidates if v["postcode"]}
            try:
                ests = get_fsa()
            except Exception as e:
                print(f"  FSA register unavailable for {label}: {e}", file=sys.stderr)
                ests = []
            for est in ests:
                v = fsa.to_venue(est, label)
                if not v or not v["name"] or fsa.match_key(v["name"], v["postcode"]) in known:
                    continue
                if circle and not (v["lat"] and areas.within(circle[0], (v["lat"], v["lon"]), circle[1])):
                    continue
                candidates.append(v)
                fsa_added += 1
        for v in candidates:
            v["categories"] = categorize(v["tags"])
            if wanted and not wanted & set(v["categories"]):
                continue
            # the same venue is often mapped twice (a node and a building outline)
            keys = {(v["name"].lower(), v["postcode"].lower() or v["address"].lower(), v["website"].lower())}
            if v["lat"] and v["lon"]:
                keys.add((v["name"].lower(), round(v["lat"], 3), round(v["lon"], 3)))
            if keys & seen:
                continue
            seen |= keys
            venues.append(v)
            kept += 1
        print(f"[{i}/{len(units)}] {label}: {kept} venues ({fsa_added} extra from the FSA register, "
              f"{closed} closed skipped)", file=sys.stderr)
    return venues


def enrich(venues, cache, workers, google_key):
    if google_key:
        missing = [v for v in venues if not v["website"] and not v["email"]]
        print(f"Google Places lookup for {len(missing)} venues without a website", file=sys.stderr)
        with cf.ThreadPoolExecutor(8) as ex:
            for v, (site, status) in zip(missing, ex.map(lambda v: places.lookup_website(
                    v["name"], f"{v['address']} {v['postcode']}", google_key, v["lat"], v["lon"]), missing)):
                v["website"] = site
                v["closed"] = status == "CLOSED_PERMANENTLY"

    # Crawl every venue website, including ones with an OSM email, to catch closed venues / dead sites.
    todo = {}
    for v in venues:
        if v["website"] and not emails.is_aggregator(v["website"]) and cache.get(v["website"]) is None:
            todo.setdefault(CrawlCache.key(v["website"]), v["website"])
    print(f"Crawling {len(todo)} websites for email addresses", file=sys.stderr)

    def work(url):
        _, found, status, final_url = emails.find_email(url)
        cache.put(url, found, status, final_url)

    with cf.ThreadPoolExecutor(workers) as ex:
        futures = [ex.submit(work, u) for u in todo.values()]
        for n, _ in enumerate(cf.as_completed(futures), 1):
            if n % 100 == 0:
                print(f"  crawled {n}/{len(futures)}", file=sys.stderr)

    for v in venues:
        if v["website"]:
            rec = cache.get(v["website"]) or {}
            if rec.get("status") in CLOSED_STATUSES:
                v["closed"] = True
                continue
            found = emails.valid_emails(rec.get("emails", []), [v["website"], rec.get("final_url", "")])
            if not v["email"] and found:
                v["email"], v["email_source"] = found[0], "venue website"
            v["other_emails"] = [e for e in v["other_emails"] + found if e != v["email"]]


def find_websites(venues, cache_dir, limit, workers):
    """Verified website discovery for venues with none, busiest-missing neighbourhoods first."""
    cache = discover.Cache(os.path.join(cache_dir, "discover.jsonl"))
    missing = [v for v in venues if not v["website"] and not v.get("closed")]
    per_area = Counter((v["borough"], v.get("area", "")) for v in missing)
    missing.sort(key=lambda v: (-v.get("english_hint", 0), -per_area[(v["borough"], v.get("area", ""))],
                                v["borough"], v.get("area", "")))
    todo = [v for v in missing if discover.usable(v["name"]) and cache.get(v) is None]
    if limit:
        todo = todo[:limit]
    print(f"Website discovery: {len(missing)} venues without a website, {len(todo)} to try this run", file=sys.stderr)
    dns = discover.DNS()

    def work(v):
        site, why, tried = discover.discover(v, dns)
        cache.put(v, site, why, len(tried))

    done = 0
    with cf.ThreadPoolExecutor(workers) as ex:
        for f in cf.as_completed([ex.submit(work, v) for v in todo]):
            f.result()
            done += 1
            if done % 250 == 0:
                with cache.lock:
                    hits = sum(1 for r in cache.data.values() if r["website"])
                print(f"  discovery {done}/{len(todo)} ({hits} websites found so far)", file=sys.stderr)
    found = 0
    for v in missing:
        rec = cache.get(v)
        if rec and rec["website"]:
            v["website"], v["website_type"] = rec["website"], f"own site (found by name, {rec['evidence']} verified)"
            found += 1
    print(f"Website discovery: {found} venues now have a verified website", file=sys.stderr)


EMAIL_FIELDS = ["email", "name", "borough", "area", "categories", "website", "other_emails", "email_source", "source", "locations",
                "phone", "address", "postcode", "osm_url"]
SOCIAL_FIELDS = ["social_url", "name", "borough", "area", "categories", "facebook", "instagram", "locations",
                 "phone", "address", "postcode", "source", "osm_url"]
WEBSITE_FIELDS = ["website", "website_type", "name", "borough", "area", "categories", "email", "locations", "phone", "address",
                  "postcode", "source", "osm_url"]


def website_key(url):
    return re.sub(r"^https?://(www\.)?", "", url.lower().strip()).split("#")[0].split("?")[0].rstrip("/")


def dedupe(rows, key):
    """One row per unique key (email or website); `locations` counts how many venues share it (chains)."""
    out, index = [], {}
    for r in rows:
        k = key(r)
        if k in index:
            index[k]["locations"] += 1
            continue
        r = {**r, "locations": 1}
        index[k] = r
        out.append(r)
    return out


def write_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({**r, "categories": "; ".join(r["categories"]), "other_emails": "; ".join(r["other_emails"])})


def split_osm_email(v):
    """OSM `email` can hold several addresses separated by ';'. Keep valid ones exactly as published."""
    found = [e for e in (emails.clean(x) for x in re.split(r"[;,\s]+", v["email"])) if e]
    v["email"] = found[0] if found else ""
    v["other_emails"] = found[1:]
    v["email_source"] = "openstreetmap" if found else ""
    v["website"] = emails.normalize_url(v["website"])
    if v["website"] and emails.not_venue_site(v["website"]):
        v["social_website"] = v["website"]  # a Facebook / Instagram page goes to the social file instead
        v["website"] = ""  # a Facebook / Instagram / Just Eat link is not the venue's website
    v["website_type"] = "own site" if v["website"] else ""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--boroughs", nargs="*", help="subset of boroughs (substring match), default: all 33")
    ap.add_argument("--categories", nargs="*", choices=CATEGORIES, metavar="CAT",
                    help=f"only keep these categories: {', '.join(CATEGORIES)}")
    ap.add_argument("--out", default="output", help="output directory")
    ap.add_argument("--cache", default=".cache", help="cache directory (OSM + crawl results)")
    ap.add_argument("--workers", type=int, default=16, help="parallel website crawlers")
    ap.add_argument("--no-crawl", action="store_true", help="skip crawling venue websites")
    ap.add_argument("--no-fsa", action="store_true", help="don't add venues from the FSA hygiene-rating register")
    ap.add_argument("--discover", action="store_true",
                    help="find websites for venues without one (name-based domains, verified by postcode/phone)")
    ap.add_argument("--discover-limit", type=int, default=0, help="max new venues to try this run (0 = all)")
    ap.add_argument("--discover-workers", type=int, default=96)
    ap.add_argument("--google-key", default=os.environ.get("GOOGLE_MAPS_API_KEY"),
                    help="Google Places API key to fill missing websites (or set GOOGLE_MAPS_API_KEY)")
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    os.makedirs(args.cache, exist_ok=True)
    try:
        places = areas.fetch_places(args.cache)
    except Exception as e:
        print(f"Neighbourhood lookup unavailable ({e}); the area column will use postcode districts", file=sys.stderr)
        places = []
    full_run = not args.boroughs
    venues = collect(resolve_boroughs(args.boroughs), args.cache, set(args.categories or []), not args.no_fsa,
                     EXTRA_AREAS if full_run else (), places)
    area_list = areas.locate(areas.load_areas(), places, venues)
    areas.assign(venues, area_list)
    for v in venues:
        split_osm_email(v)
    wd = extra.add_wikidata(venues, args.cache)
    chains = extra.add_brand_sites(venues)
    print(f"Websites added: {wd} from Wikidata, {chains} chain branches", file=sys.stderr)
    if args.discover:
        find_websites(venues, args.cache, args.discover_limit, args.discover_workers)
    if not args.no_crawl:
        enrich(venues, CrawlCache(os.path.join(args.cache, "crawl.jsonl")), args.workers, args.google_key)

    closed = sum(1 for v in venues if v.get("closed"))
    open_venues = sorted((v for v in venues if not v.get("closed")), key=lambda r: (r["borough"], r["name"].lower()))
    rejected = validate.validate_venues(open_venues, args.cache)
    validate.write_rejected(os.path.join(args.out, "london_rejected_emails.csv"), rejected)
    for v in open_venues:
        social.collect(v)
    social_rows = dedupe([v for v in open_venues if not v["email"] and not v["website"] and v["social_url"]],
                         lambda r: r["social_url"])
    email_rows = dedupe([v for v in open_venues if v["email"]], lambda r: r["email"])
    site_rows = dedupe([v for v in open_venues if v["website"] and not emails.not_venue_site(v["website"])],
                       lambda r: website_key(r["website"]))

    write_csv(os.path.join(args.out, "london_food_emails.csv"), email_rows, EMAIL_FIELDS)
    write_csv(os.path.join(args.out, "london_food_websites.csv"), site_rows, WEBSITE_FIELDS)
    write_csv(os.path.join(args.out, "london_food_social.csv"), social_rows, SOCIAL_FIELDS)
    by_borough = os.path.join(args.out, "by_borough")
    os.makedirs(by_borough, exist_ok=True)
    for b in sorted({v["borough"] for v in open_venues}):
        name = b.replace(" ", "_")
        write_csv(os.path.join(by_borough, f"{name}_emails.csv"), [r for r in email_rows if r["borough"] == b], EMAIL_FIELDS)
        write_csv(os.path.join(by_borough, f"{name}_websites.csv"), [r for r in site_rows if r["borough"] == b], WEBSITE_FIELDS)

    with open(os.path.join(args.out, "area_summary.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["borough", "area", "emails", "websites"])
        w.writeheader()
        w.writerows(r for r in areas.summary(email_rows, site_rows, area_list)
                    if full_run or r["borough"] in {v["borough"] for v in venues})

    either = sum(1 for v in open_venues if v["email"] or v["website"])
    print(f"\n{len(venues)} venues found, {closed} dropped as closed (dead/closed website), "
          f"{either} open venues with an email or website -> {len(email_rows)} unique emails, "
          f"{len(site_rows)} unique websites, {len(social_rows)} Facebook/Instagram-only venues, "
          f"{len(rejected)} emails rejected by validation -> {args.out}/", file=sys.stderr)


if __name__ == "__main__":
    main()
