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
    p = slug(place.split("(")[0].split("/")[0]) if place else ""
    stems = [(base, TLDS)]
    if hyphen:
        stems.append((hyphen, (".co.uk", ".com")))
    if p and p not in base:
        stems.append((base + p, TLDS))
    if "london" not in base:
        stems.append((base + "london", (".co.uk", ".com")))
    if name.lower().startswith("the "):
        stems.append(("the" + base, (".co.uk", ".com")))
    for word, cats in (("restaurant", {"Restaurant"}), ("cafe", {"Cafe", "Breakfast"}), ("pub", {"Pub"}),
                       ("bakery", {"Bakery"}), ("kitchen", set())):
        if word not in base and (cats & set(categories) or word == "kitchen"):
            stems.append((base + word, (".co.uk", ".com")))
    out = []
    for stem, tlds in stems:
        for tld in tlds:
            d = stem + tld
            if d not in out and len(stem) <= 50:
                out.append(d)
    return out[:24]


class DNS:
    """DNS-over-HTTPS lookups (the egress proxy hides resolver errors), cached and rate limited."""

    SERVERS = ("https://dns.google/resolve", "https://cloudflare-dns.com/dns-query")

    def __init__(self, qps=80):
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


def evidence(page_text, venue):
    """Why this page belongs to the venue ('postcode' / 'phone'), or '' if it doesn't."""
    flat = re.sub(r"\s+", "", page_text.upper())
    pc = re.sub(r"\s+", "", (venue.get("postcode") or "").upper())
    if len(pc) >= 5 and pc in flat:
        return "postcode"
    phone = _digits(venue.get("phone"))
    if phone.startswith("44"):
        phone = "0" + phone[2:]
    if len(phone) >= 10:
        digits = _digits(page_text)
        if phone in digits or phone[1:] in digits:
            return "phone"
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
                    rec = json.loads(line)
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
