"""Find websites for venues that have none listed, without guessing.

For each venue, candidate domains are built from its name (e.g. "Pablo's Pizza" in Barking ->
pablospizza.co.uk, pablospizzabarking.co.uk, ...). A candidate is only accepted when the domain
exists AND the site's own pages show the venue's postcode or phone number, which proves it is the
same business. Anything else is discarded.
"""

import json
import os
import re
import threading
import time
import unicodedata
import urllib.parse

from . import emails, http

TLDS = (".co.uk", ".com", ".uk", ".london")
# Per-city settings (see cities/*.json); the defaults are London's.
SETTINGS = {"tlds": TLDS, "country_code": "44", "min_phone_digits": 10, "postcode_needs_name": False,
            "city_word": "london", "city_aliases": (), "city_evidence": ""}
PLACE_SUFFIX = re.compile(r"\b(district|khet|amphoe|borough|county|city|province)\b", re.I)


def configure(tlds=None, country_code=None, min_phone_digits=None, postcode_needs_name=None, city_word=None,
              city_aliases=None, city_evidence=None):
    """city_evidence: regex; when set, a page showing the venue's full name and matching it also counts
    (weaker than postcode / phone, so it is labelled 'name + city' in the output)."""
    for k, v in (("tlds", tuple(tlds) if tlds else None), ("country_code", country_code),
                 ("min_phone_digits", min_phone_digits), ("postcode_needs_name", postcode_needs_name),
                 ("city_word", city_word), ("city_aliases", tuple(city_aliases) if city_aliases else None),
                 ("city_evidence", city_evidence)):
        if v is not None:
            SETTINGS[k] = v
PERSON_RX = re.compile(r"^(mr|mrs|ms|miss|dr)\b\.?", re.I)
DROP_WORDS = re.compile(r"\b(ltd|limited|plc|llp|t/a|ta|trading as|uk|the)\b", re.I)
GENERIC = {"cafe", "restaurant", "kitchen", "bar", "pub", "grill", "takeaway", "food", "foods", "pizza",
           "chicken", "fish", "chips", "coffee", "bakery", "express", "house", "shop", "store", "catering",
           "canteen", "club", "social", "sports", "school", "limited", "london"}
NEEDS_JS = re.compile(r"enable javascript|you need to enable javascript", re.I)


def slug(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    text = text.replace("&", "and").replace("'", "")
    return re.sub(r"[^a-z0-9]", "", text)


def usable(name):
    """Skip names that can't identify a business domain (people's names, generic words)."""
    if not name or PERSON_RX.match(name.strip()):
        return False
    words = [w for w in re.findall(r"[a-z0-9]+", slug_words(name))]
    return any(w not in GENERIC and len(w) >= 3 for w in words)


def slug_words(name):
    return DROP_WORDS.sub(" ", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower())


def candidates(name, place="", categories=()):
    """Candidate domains for a venue, most likely first (at most ~24)."""
    clean = DROP_WORDS.sub(" ", name.split("(")[0].split(" - ")[0])
    base = slug(clean)
    if not usable(name) or len(base) < 4 or len(base) > 40:
        return []
    words = re.findall(r"[a-z0-9]+", slug_words(clean).replace("&", " and ").replace("'", ""))
    hyphen = "-".join(words) if len(words) > 1 else ""
    p = slug(PLACE_SUFFIX.sub(" ", place.split("(")[0].split("/")[0])) if place else ""
    tlds = SETTINGS["tlds"]
    top2 = tlds[:2]
    city = SETTINGS["city_word"]
    stems = [(base, tlds)]
    if hyphen:
        stems.append((hyphen, top2))
    if p and p not in base:
        stems.append((base + p, tlds))
    if city and city not in base:
        stems.append((base + city, top2))
    if name.lower().startswith("the "):
        stems.append(("the" + base, top2))
    for alias in SETTINGS["city_aliases"]:
        if alias not in base:
            stems.append((base + alias, top2))
            stems.append(((hyphen or base) + "-" + alias, top2))
    for word, cats in (("restaurant", {"Restaurant"}), ("cafe", {"Cafe", "Breakfast"}), ("pub", {"Pub"}),
                       ("bakery", {"Bakery"}), ("kitchen", set())):
        if word not in base and (cats & set(categories) or word == "kitchen"):
            stems.append((base + word, top2))
    out = []
    for stem, tlds in stems:
        for tld in tlds:
            d = stem + tld
            if d not in out and len(stem) <= 50:
                out.append(d)
    return out[:30]


class DNS:
    """DNS-over-HTTPS lookups (the egress proxy hides resolver errors), cached and rate limited."""

    SERVERS = ("https://dns.google/resolve", "https://cloudflare-dns.com/dns-query")

    def __init__(self, qps=120):
        self.cache, self.lock, self.interval, self.next, self.n = {}, threading.Lock(), 1.0 / qps, 0.0, 0

    def lookup(self, host):
        """'yes' (has an address), 'empty' (domain registered, no A record), 'no' (doesn't exist), None (unsure)."""
        if host in self.cache:
            return self.cache[host]
        with self.lock:
            wait = self.next - time.monotonic()
            self.next = max(self.next, time.monotonic()) + self.interval
            self.n += 1
            order = self.SERVERS if self.n % 2 else self.SERVERS[::-1]
        if wait > 0:
            time.sleep(wait)
        res = None
        for server in order:
            try:
                _, text = http.get(f"{server}?name={host}&type=A", headers={"Accept": "application/dns-json"},
                                   timeout=10, max_bytes=100_000)
                data = json.loads(text)
                if data.get("Status") == 3:
                    res = "no"
                elif data.get("Status") == 0:
                    res = "yes" if any(a.get("type") in (1, 5) for a in data.get("Answer", [])) else "empty"
                break
            except Exception:
                continue
        self.cache[host] = res
        return res

    def exists(self, domain):
        r = self.lookup(domain)
        return r == "yes" or (r in ("empty", None) and self.lookup("www." + domain) == "yes")


def _digits(s):
    return re.sub(r"\D", "", s or "")


def _name_on_page(flat, venue):
    words = [w for w in re.findall(r"[A-Z0-9]+", slug_words(venue.get("name") or "").upper())
             if len(w) >= 4 and w.lower() not in GENERIC]
    return any(w in flat for w in words)


def evidence(page_text, venue):
    """Why this page belongs to the venue ('postcode' / 'phone'), or '' if it doesn't."""
    flat = re.sub(r"\s+", "", page_text.upper())
    pc = re.sub(r"\s+", "", (venue.get("postcode") or "").upper())
    if len(pc) >= 5 and pc in flat and (not SETTINGS["postcode_needs_name"] or _name_on_page(flat, venue)):
        return "postcode"
    phone = _digits(venue.get("phone"))
    cc = SETTINGS["country_code"]
    if phone.startswith(cc):
        phone = "0" + phone[len(cc):]
    if len(phone) >= SETTINGS["min_phone_digits"]:
        digits = _digits(page_text)
        if phone in digits or phone[1:] in digits:
            return "phone"
    if SETTINGS["city_evidence"]:
        name = slug(DROP_WORDS.sub(" ", (venue.get("name") or "").split("(")[0]))
        if len(name) >= 6 and name in slug(page_text) and re.search(SETTINGS["city_evidence"], page_text, re.I):
            return "name + city"
    return ""


def verify(domain, venue):
    """Fetch the candidate site (+ a contact page) and return (url, evidence, page) if it proves to be the venue."""
    for url in (f"https://{domain}/", f"https://www.{domain}/", f"http://{domain}/"):
        try:
            final_url, page = http.get(url, timeout=12)
            break
        except Exception:
            continue
    else:
        return None
    if emails.is_aggregator(final_url):
        return None
    target = emails.soft_redirect(page)
    if target and not re.search(r"/lander\b", target):
        try:
            final_url, page = http.get(urllib.parse.urljoin(final_url, target), timeout=12)
        except Exception:
            pass
    pages = [page]
    for link in emails.contact_links(page, final_url, limit=3):
        try:
            pages.append(http.get(link, timeout=12)[1])
        except Exception:
            continue
    for p in pages:
        why = evidence(emails.visible_text(p) + " " + p, venue)
        if why:
            return final_url, why
    return None


class Cache:
    def __init__(self, path):
        self.path, self.lock, self.data = path, threading.Lock(), {}
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except ValueError:  # last line cut short by a crash / container restart
                        continue
                    self.data[rec["key"]] = rec

    @staticmethod
    def key(v):
        pc = re.sub(r"\s", "", (v.get("postcode") or "").upper())
        return f"{v['name'].lower()}|{pc}"

    def get(self, v):
        return self.data.get(self.key(v))

    def put(self, v, website, why, tried):
        rec = {"key": self.key(v), "website": website, "evidence": why, "tried": tried}
        with self.lock:
            self.data[rec["key"]] = rec
            with open(self.path, "a") as f:
                f.write(json.dumps(rec) + "\n")


def discover(v, dns):
    """Return (website, evidence, tried_domains) for one venue."""
    tried = []
    for domain in candidates(v["name"], v.get("area", ""), v.get("categories", ())):
        tried.append(domain)
        if dns.exists(domain):
            found = verify(domain, v)
            if found:
                return found[0], found[1], tried
    return "", "", tried
