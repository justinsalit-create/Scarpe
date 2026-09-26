"""Tiny stdlib HTTP helpers."""

import gzip
import urllib.error
import urllib.request

USER_AGENT = "LondonFoodContacts/1.0 (+contact-research; respects robots.txt)"


def get(url, timeout=15, data=None, headers=None, max_bytes=3_000_000):
    """Return (final_url, text) or raise urllib.error.URLError / OSError."""
    hdrs = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip", "Accept-Language": "en-GB,en;q=0.8"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(max_bytes)
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.geturl(), raw.decode(charset, errors="replace")
