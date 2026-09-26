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

from . import emails, osm, places
from .boroughs import BOROUGHS, short_name
from .categories import CATEGORIES, categorize

CLOSED_STATUSES = ("closed", "parked", "dead")

FIELDS = ["borough", "name", "categories", "contact", "email", "email_source", "website", "locations",
          "phone", "address", "postcode", "facebook", "instagram", "osm_url", "lat", "lon"]


class CrawlCache:
    """Per-website crawl results persisted as JSON lines so interrupted runs resume."""

    def __init__(self, path):
        self.path, self.lock, self.data = path, threading.Lock(), {}
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    rec = json.loads(line)
                    if "emails" in rec:  # older cache formats: re-crawl them
                        self.data[rec["key"]] = rec

    @staticmethod
    def key(url):
        p = urllib.parse.urlparse(emails.normalize_url(url))
        return (p.netloc.lower().removeprefix("www.") + p.path.rstrip("/")).lower()

    def get(self, url):
        return self.data.get(self.key(url))

    def put(self, url, emails_found, status, final_url):
        rec = {"key": self.key(url), "emails": emails_found, "status": status, "final_url": final_url}
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


def collect(boroughs, cache_dir, wanted):
    venues, seen = [], set()
    for i, b in enumerate(boroughs, 1):
        elements = osm.fetch_borough(b, cache_dir)
        kept = closed = 0
        for el in elements:
            v = osm.to_venue(el, short_name(b))
            if not v["name"]:
                continue
            if osm.is_closed(v["tags"]):
                closed += 1
                continue
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
        print(f"[{i}/{len(boroughs)}] {short_name(b)}: {kept} venues ({closed} closed skipped)", file=sys.stderr)
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
        v["email_source"] = "openstreetmap" if v["email"] else ""
        if v["website"] and cache.get(v["website"]) is None:
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
            elif not v["email"]:
                email = emails.best_email(rec.get("emails", []), [v["website"], rec.get("final_url", "")])
                if email:
                    v["email"], v["email_source"] = email, "venue website"
        v["contact"] = v["email"] or emails.normalize_url(v["website"])


def contact_key(contact):
    """Normalise an email / website so the same business is only listed once."""
    c = contact.lower().strip()
    if "@" in c and "/" not in c:
        return c
    c = re.sub(r"^https?://(www\.)?", "", c).split("#")[0].split("?")[0]
    return c.rstrip("/")


def dedupe(rows):
    """One row per unique email / website; `locations` counts how many venues share it (chains)."""
    out, index = [], {}
    for r in rows:
        k = contact_key(r["contact"]) if r["contact"] else None
        if k is not None and k in index:
            index[k]["locations"] += 1
            continue
        r["locations"] = 1
        if k is not None:
            index[k] = r
        out.append(r)
    return out


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({**r, "categories": "; ".join(r["categories"]), "website": emails.normalize_url(r["website"])})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--boroughs", nargs="*", help="subset of boroughs (substring match), default: all 33")
    ap.add_argument("--categories", nargs="*", choices=CATEGORIES, metavar="CAT",
                    help=f"only keep these categories: {', '.join(CATEGORIES)}")
    ap.add_argument("--out", default="output", help="output directory")
    ap.add_argument("--cache", default=".cache", help="cache directory (OSM + crawl results)")
    ap.add_argument("--workers", type=int, default=16, help="parallel website crawlers")
    ap.add_argument("--no-crawl", action="store_true", help="skip crawling venue websites")
    ap.add_argument("--keep-all", action="store_true", help="also output venues with no email or website")
    ap.add_argument("--google-key", default=os.environ.get("GOOGLE_MAPS_API_KEY"),
                    help="Google Places API key to fill missing websites (or set GOOGLE_MAPS_API_KEY)")
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    os.makedirs(args.cache, exist_ok=True)
    venues = collect(resolve_boroughs(args.boroughs), args.cache, set(args.categories or []))
    if args.no_crawl:
        for v in venues:
            v["email_source"] = "openstreetmap" if v["email"] else ""
            v["contact"] = v["email"] or emails.normalize_url(v["website"])
            v["closed"] = False
    else:
        enrich(venues, CrawlCache(os.path.join(args.cache, "crawl.jsonl")), args.workers, args.google_key)

    closed = sum(1 for v in venues if v.get("closed"))
    open_venues = [v for v in venues if not v.get("closed")]
    rows = open_venues if args.keep_all else [v for v in open_venues if v["contact"]]
    rows.sort(key=lambda r: (r["borough"], r["name"].lower()))
    before = len(rows)
    rows = dedupe(rows)
    write_csv(os.path.join(args.out, "london_food_contacts.csv"), rows)
    by_borough = os.path.join(args.out, "by_borough")
    os.makedirs(by_borough, exist_ok=True)
    for b in sorted({r["borough"] for r in rows}):
        write_csv(os.path.join(by_borough, b.replace(" ", "_") + ".csv"), [r for r in rows if r["borough"] == b])

    with_email = sum(1 for r in rows if r["email"])
    print(f"\n{len(venues)} venues found, {closed} dropped as closed (dead/closed website), "
          f"{before - len(rows)} duplicate contacts merged, {len(rows)} unique contacts "
          f"({with_email} emails, {len(rows) - with_email} website only) -> {args.out}/", file=sys.stderr)


if __name__ == "__main__":
    main()
