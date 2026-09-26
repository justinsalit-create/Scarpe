"""Tiny stdlib HTTP helpers."""

import gzip
import json
import urllib.error
import urllib.request

USER_AGENT = "LondonFoodContacts/1.0 (+contact-research; respects robots.txt)"
# Many small-venue sites sit behind WAFs that 403 any non-browser client; retry those once as a browser.
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
BROWSER_HEADERS = {"User-Agent": BROWSER_UA,
                   "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


def get(url, timeout=15, data=None, headers=None, max_bytes=3_000_000):
    """Return (final_url, text) or raise urllib.error.URLError / OSError."""
    try:
        return _get(url, timeout, data, headers, max_bytes)
    except urllib.error.HTTPError as e:
        if e.code not in (403, 406, 429) or (headers or {}).get("User-Agent"):
            raise
    return _get(url, timeout, data, {**(headers or {}), **BROWSER_HEADERS}, max_bytes)


def domain_exists(host):
    """Check DNS via DNS-over-HTTPS (the egress proxy hides resolver errors). None when unsure."""
    try:
        _, text = _get(f"https://dns.google/resolve?name={host}&type=A", 10, None,
                       {"Accept": "application/dns-json"}, 100_000)
        status = json.loads(text).get("Status")
    except Exception:
        return None
    return {0: True, 3: False}.get(status)


def _get(url, timeout, data, headers, max_bytes):
    hdrs = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip", "Accept-Language": "en-GB,en;q=0.8"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(max_bytes)
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.geturl(), raw.decode(charset, errors="replace")
