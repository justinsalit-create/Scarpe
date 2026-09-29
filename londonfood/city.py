"""Venue emails / websites for any city described by cities/<slug>.json (London uses the older cli.py).

    python -m londonfood.city bangkok                 # map data + website crawl
    python -m londonfood.city bangkok --discover      # also verified website discovery
    python -m londonfood.city bangkok --units Vadhana "Khlong Toei"

Outputs go to output/<slug>/ and the cache to ~/.foodcontacts-cache/<slug>/ (see PLAYBOOK.md).
When "english_priority" is set, every venue gets an English-friendliness score and the files are
sorted with the most English-speaking / Americanized venues first.
"""

import argparse
import concurrent.futures as cf
import csv
import json
import os
import re
import sys
import urllib.parse

from . import discover, emails, extra, http, names, osm, validate
from .categories import categorize
from .cli import CLOSED_STATUSES, CrawlCache, dedupe, find_websites, split_osm_email, website_key

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WESTERN = {"Burger", "Pizza", "American", "Italian", "Mexican", "Tacos", "Steak", "BBQ", "French", "Spanish",
           "Greek", "Bagels", "Donuts", "Breakfast", "Pub", "Western", "Deli"}


def load_config(slug):
    with open(os.path.join(ROOT, "cities", f"{slug}.json")) as f:
        return json.load(f)


def fetch_units(cfg, cache_dir):
    """Admin areas (districts) inside the city's region, cached."""
    path = os.path.join(cache_dir, "osm", "units.json")
    if not os.path.exists(path):
        q = (f'[out:json][timeout:240];area({cfg["region_area_id"]})->.r;'
             f'rel(area.r)["boundary"="administrative"]["admin_level"="{cfg["unit_admin_level"]}"];out tags;')
        body = urllib.parse.urlencode({"data": q}).encode()
        for url in osm.OVERPASS_URLS * 2:
            try:
                els = json.loads(http.get(url, timeout=300, data=body, max_bytes=50_000_000)[1])["elements"]
                break
            except Exception as e:
                last = e
        else:
            raise RuntimeError(f"could not list districts: {last}")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(els, f)
    with open(path) as f:
        els = json.load(f)
    units = sorted({(e["tags"].get("name:en") or e["tags"].get("name"), e["id"]) for e in els
                    if (e["tags"].get("name:en") or e["tags"].get("name")) not in cfg.get("exclude_units", [])})
    if cfg.get("expected_units") and len(units) != cfg["expected_units"]:
        print(f"  note: found {len(units)} districts, expected {cfg['expected_units']}", file=sys.stderr)
    return units


def collect(cfg, units, cache_dir):
    local = re.compile(cfg.get("local_script") or r"(?!x)x")
    venues, seen, missing = [], set(), []
    for i, (label, rel_id) in enumerate(units, 1):
        kept = closed = 0
        try:
            elements = osm.fetch_area(f"{cfg['slug']}_{label}", 3600000000 + rel_id, cache_dir)
        except Exception as e:  # map server overloaded: carry on, the next run retries this district
            print(f"[{i}/{len(units)}] {label}: not downloaded yet ({e})", file=sys.stderr)
            missing.append(label)
            continue
        for el in elements:
            v = osm.to_venue(el, label)
            if not v["name"]:
                continue
            if osm.is_closed(v["tags"]):
                closed += 1
                continue
            # show the English name where the map has one; keep the local-script name alongside
            v["name_local"] = v["name"] if local.search(v["name"]) else ""
            v["latin_name"] = not local.search(v["name"])
            if v["name_local"] and v["name_en"]:
                v["name"] = v["name_en"]
            v["area"], v["source"] = label, "openstreetmap"
            v["categories"] = categorize({**v["tags"], "name": v["name"]})
            keys = {(v["name"].lower(), v["postcode"].lower() or v["address"].lower(), v["website"].lower())}
            if v["lat"] and v["lon"]:
                keys.add((v["name"].lower(), round(v["lat"], 3), round(v["lon"], 3)))
            if keys & seen:
                continue
            seen |= keys
            venues.append(v)
            kept += 1
        print(f"[{i}/{len(units)}] {label}: {kept} venues ({closed} closed skipped)", file=sys.stderr)
    print(f"Districts not downloaded yet: {len(missing)}", file=sys.stderr)
    return venues


class LangCrawlCache(CrawlCache):
    """Crawl cache that also remembers the homepage language."""

    def put(self, url, emails_found, status, final_url, lang=""):
        rec = {"key": self.key(url), "emails": emails_found, "status": status, "final_url": final_url,
               "lang": lang, "v": 3}
        with self.lock:
            self.data[rec["key"]] = rec
            with open(self.path, "a") as f:
                f.write(json.dumps(rec) + "\n")


def crawl(venues, cache, workers):
    todo = {}
    for v in venues:
        if v["website"] and not emails.is_aggregator(v["website"]) and cache.get(v["website"]) is None:
            todo.setdefault(CrawlCache.key(v["website"]), v["website"])
    print(f"Crawling {len(todo)} websites for emails and language", file=sys.stderr)

    def work(url):
        info = {}
        _, found, status, final_url = emails.find_email(url, info)
        cache.put(url, found, status, final_url, info.get("lang", ""))

    with cf.ThreadPoolExecutor(workers) as ex:
        for n, f in enumerate(cf.as_completed([ex.submit(work, u) for u in todo.values()]), 1):
            f.result()
            if n % 100 == 0:
                print(f"  crawled {n}/{len(todo)}", file=sys.stderr)
    for v in venues:
        v["site_language"] = ""
        if not v["website"]:
            continue
        rec = cache.get(v["website"]) or {}
        if rec.get("status") in CLOSED_STATUSES:
            v["closed"] = True
            continue
        v["site_language"] = rec.get("lang", "")
        found = emails.valid_emails(rec.get("emails", []), [v["website"], rec.get("final_url", "")])
        if not v["email"] and found:
            v["email"], v["email_source"] = found[0], "venue website"
        v["other_emails"] = [e for e in v["other_emails"] + found if e != v["email"]]


def english_score(v):
    """How likely the venue is English-speaking / Americanized, with the reasons."""
    score, why = 0, []
    lang = v.get("site_language")
    if lang == "English":
        score, why = score + 3, why + ["website in English"]
    elif lang == "Bilingual":
        score, why = score + 2, why + ["website has English"]
    western = sorted(WESTERN & set(v["categories"]))
    if western:
        score, why = score + 2, why + ["Western food (" + ", ".join(western) + ")"]
    if v.get("latin_name"):
        score, why = score + 1, why + ["English/Latin-script name"]
    v["english_score"] = score
    v["english_priority"] = "High" if score >= 4 else "Medium" if score >= 2 else "Low"
    # english=yes: English/bilingual website, or an English name serving Western food
    v["english"] = "yes" if score >= 3 else "no"
    v["why"] = "; ".join(why)


FIELDS_COMMON = ["name", "name_local", "district", "categories", "english", "english_priority", "english_score",
                 "site_language", "why"]
EMAIL_FIELDS = ["email"] + FIELDS_COMMON + ["website", "other_emails", "email_source", "locations", "phone",
                                            "address", "postcode", "source", "osm_url"]
WEBSITE_FIELDS = ["website", "website_type"] + FIELDS_COMMON + ["email", "locations", "phone", "address",
                                                                "postcode", "source", "osm_url"]


MASTER_FIELDS = ["name", "name_local", "district", "categories", "english", "english_score", "site_language",
                 "why", "email", "other_emails", "email_check", "website", "website_type", "phone", "address",
                 "postcode", "lat", "lon", "source", "osm_url", "merged"]


def validate_emails(venues, cache_dir):
    """Keep only emails whose syntax and mail servers check out; returns the rejected ones."""
    mx = validate.MXChecker(os.path.join(cache_dir, "mx.jsonl"))
    todo = {e for v in venues for e in [v["email"]] + v["other_emails"] if e}
    with cf.ThreadPoolExecutor(32) as ex:
        verdict = dict(zip(todo, ex.map(mx.check, todo)))
    rejected = []
    for v in venues:
        cands = [e for e in [v["email"]] + v["other_emails"] if e]
        good = [e for e in cands if verdict[e] == "valid"]
        rejected += [{"email": e, "reason": verdict[e], "name": v["name"], "district": v["borough"]}
                     for e in cands if verdict[e] != "valid"]
        v["email"], v["other_emails"] = (good[0], good[1:]) if good else ("", [])
        v["email_check"] = "mx ok" if good else ""
    return rejected


def write_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({**r, "district": r["borough"], "categories": "; ".join(r["categories"]),
                        "other_emails": "; ".join(r["other_emails"]), "merged": r.get("merged", 1)})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("city", help="config name in cities/, e.g. bangkok")
    ap.add_argument("--units", nargs="*", help="only these districts (substring match)")
    ap.add_argument("--out", help="output directory (default output/<city>)")
    ap.add_argument("--cache", help="cache directory (default ~/.foodcontacts-cache/<city>)")
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--no-crawl", action="store_true")
    ap.add_argument("--discover", action="store_true", help="verified website discovery for venues without one")
    ap.add_argument("--discover-limit", type=int, default=0)
    ap.add_argument("--discover-workers", type=int, default=96)
    args = ap.parse_args(argv)

    cfg = load_config(args.city)
    slug = cfg["slug"]
    out = args.out or os.path.join(ROOT, "output", slug)
    cache_dir = args.cache or os.path.join(os.path.expanduser("~"), ".foodcontacts-cache", slug)
    os.makedirs(out, exist_ok=True)
    os.makedirs(os.path.join(cache_dir, "osm"), exist_ok=True)
    discover.configure(tlds=cfg.get("tlds"), country_code=cfg.get("country_code"),
                       min_phone_digits=cfg.get("min_phone_digits"),
                       postcode_needs_name=cfg.get("postcode_needs_name"), city_word=cfg.get("city_word"),
                       city_aliases=cfg.get("city_aliases"), city_evidence=cfg.get("city_evidence"))

    units = fetch_units(cfg, cache_dir)
    if args.units:
        units = [u for u in units if any(n.lower() in u[0].lower() for n in args.units)]
    venues = collect(cfg, units, cache_dir)
    for v in venues:
        split_osm_email(v)
        v["site_language"] = ""
    wd = extra.add_wikidata(venues, cache_dir, cfg.get("bbox"))
    chains = extra.add_brand_sites(venues)
    print(f"Websites added: {wd} from Wikidata, {chains} chain branches", file=sys.stderr)
    if args.discover:
        find_websites(venues, cache_dir, args.discover_limit, args.discover_workers)
    if not args.no_crawl:
        crawl(venues, LangCrawlCache(os.path.join(cache_dir, "crawl.jsonl")), args.workers)

    closed = sum(1 for v in venues if v.get("closed"))
    live = [v for v in venues if not v.get("closed")]
    rejected = validate_emails(live, cache_dir)
    for v in live:
        english_score(v)
    live.sort(key=lambda r: (-r["english_score"], r["borough"], r["name"].lower()))
    email_rows = dedupe([v for v in live if v["email"]], lambda r: r["email"])
    site_rows = dedupe([v for v in live if v["website"] and not emails.not_venue_site(v["website"])],
                       lambda r: website_key(r["website"]))

    # every open venue, contact or not, merged with the agreed matching rules: the handoff to the
    # partner pipeline, which hunts contacts for the venues still missing one
    master = names.dedupe_venues(list(live), cfg.get("country_code", ""))
    write_csv(os.path.join(out, f"{slug}_venues_master.csv"), master, MASTER_FIELDS)
    with open(os.path.join(out, f"{slug}_rejected_emails.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["email", "reason", "name", "district"])
        w.writeheader()
        w.writerows(rejected)
    write_csv(os.path.join(out, f"{slug}_food_emails.csv"), email_rows, EMAIL_FIELDS)
    write_csv(os.path.join(out, f"{slug}_food_websites.csv"), site_rows, WEBSITE_FIELDS)
    by = os.path.join(out, "by_district")
    os.makedirs(by, exist_ok=True)
    for d in sorted({v["borough"] for v in live}):
        name = re.sub(r"\W+", "_", d).strip("_")
        write_csv(os.path.join(by, f"{name}_emails.csv"), [r for r in email_rows if r["borough"] == d], EMAIL_FIELDS)
        write_csv(os.path.join(by, f"{name}_websites.csv"), [r for r in site_rows if r["borough"] == d], WEBSITE_FIELDS)
    with open(os.path.join(out, "district_summary.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["district", "venues", "emails", "websites", "high_priority_emails", "high_priority_websites"])
        for d in sorted({v["borough"] for v in live}):
            w.writerow([d, sum(1 for v in live if v["borough"] == d),
                        sum(1 for r in email_rows if r["borough"] == d), sum(1 for r in site_rows if r["borough"] == d),
                        sum(1 for r in email_rows if r["borough"] == d and r["english_priority"] == "High"),
                        sum(1 for r in site_rows if r["borough"] == d and r["english_priority"] == "High")])

    hi_e = sum(1 for r in email_rows if r["english_priority"] == "High")
    hi_w = sum(1 for r in site_rows if r["english_priority"] == "High")
    print(f"Master venue list: {len(master)} venues; {len(rejected)} emails rejected by validation", file=sys.stderr)
    print(f"\n{len(venues)} venues found, {closed} dropped as closed, {len(email_rows)} unique emails "
          f"({hi_e} high English priority), {len(site_rows)} unique websites ({hi_w} high) -> {out}/",
          file=sys.stderr)


if __name__ == "__main__":
    main()
