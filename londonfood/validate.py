"""Email validation: syntax, then the domain's mail servers (MX) via DNS-over-HTTPS.

An SMTP mailbox check (RCPT TO) is not possible from the cloud sandbox (only HTTPS egress), so the
check stops at MX: domains that do not exist, publish a null MX ("0 ."), or have no MX record are
rejected, since mail to them would hard-bounce.
"""

import json
import os
import re
import threading

from . import http

SYNTAX = re.compile(r"^[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
                    r"@(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")


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
        s = self.domain_status(e.split("@")[1])
        return "valid" if s in ("ok", "unknown") else s
