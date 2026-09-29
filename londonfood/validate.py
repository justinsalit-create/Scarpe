"""Email validation: syntax, then the domain's mail servers (MX) via DNS-over-HTTPS.

An SMTP mailbox check (RCPT TO) is not possible from the cloud sandbox (only HTTPS egress), so the
check stops at MX: domains that do not exist, publish a null MX ("0 ."), or have no MX record are
rejected, since mail to them would hard-bounce.
"""

import concurrent.futures as cf
import json
import os
import re
import threading

from . import http

SYNTAX = re.compile(r"^[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
                    r"@(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")


# Misspelt free-mail domains: some exist (typo-squatters) and would pass the MX check, but the venue
# meant gmail / hotmail / yahoo, so the address as written is wrong.
TYPO_DOMAINS = {"gamil.com", "gmial.com", "gmai.com", "gmail.co", "gmail.con", "gmaill.com", "gnail.com", "gmail.cm",
                "gmail.om", "gmal.com", "gmali.com", "gmeil.com", "hotmial.com", "hotmail.con", "hotmai.com",
                "hotamil.com", "hotmil.com", "hotmaill.com", "homail.com", "hotmail.co", "yaho.com", "yahooo.com",
                "yahoo.con", "yhoo.com", "outlok.com", "outloo.com", "iclod.com", "icoud.com", "windowlive.com"}


class MXChecker:
    def __init__(self, cache_path=None):
        self.path, self.lock, self.data = cache_path, threading.Lock(), {}
        if cache_path and os.path.exists(cache_path):
            with open(cache_path) as f:
                for line in f:
                    try:
                        r = json.loads(line)
                        self.data[r["domain"]] = r["status"]
                    except ValueError:
                        continue

    def domain_status(self, domain):
        """'ok', 'no_domain', 'null_mx', 'no_mx' or 'unknown' (lookup failed: treated as ok)."""
        if domain in self.data:
            return self.data[domain]
        status = "unknown"
        for server in ("https://dns.google/resolve", "https://cloudflare-dns.com/dns-query"):
            try:
                d = json.loads(http.get(f"{server}?name={domain}&type=MX", headers={"Accept": "application/dns-json"},
                                        timeout=10, max_bytes=100_000)[1])
            except Exception:
                continue
            if d.get("Status") == 3:
                status = "no_domain"
            elif d.get("Status") == 0:
                mx = [a["data"] for a in d.get("Answer", []) if a.get("type") == 15]
                if not mx:
                    status = "no_mx"
                elif all(re.match(r"^\s*0\s+\.?\s*$", m) for m in mx):
                    status = "null_mx"
                else:
                    status = "ok"
            break
        with self.lock:
            self.data[domain] = status
            if self.path and status != "unknown":
                with open(self.path, "a") as f:
                    f.write(json.dumps({"domain": domain, "status": status}) + "\n")
        return status

    def check(self, email):
        """'valid' or the reason it is not: 'bad_syntax', 'no_domain', 'null_mx', 'no_mx'."""
        e = (email or "").strip().lower()
        if not SYNTAX.match(e):
            return "bad_syntax"
        if e.split("@")[1] in TYPO_DOMAINS:
            return "typo_domain"
        s = self.domain_status(e.split("@")[1])
        return "valid" if s in ("ok", "unknown") else s


def validate_venues(venues, cache_dir):
    """Keep only emails whose syntax and mail servers check out; returns the rejected ones."""
    mx = MXChecker(os.path.join(cache_dir, "mx.jsonl"))
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


def write_rejected(path, rejected):
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["email", "reason", "name", "district"])
        w.writeheader()
        w.writerows(rejected)
