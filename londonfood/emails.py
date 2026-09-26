"""Find contact email addresses on a venue's own website."""

import html
import re
import socket
import urllib.error
import urllib.parse
import urllib.robotparser

from . import http

EMAIL_RX = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}")
MAILTO_RX = re.compile(r"mailto:([^\"'?>\s]+)", re.I)
CFEMAIL_RX = re.compile(r'data-cfemail="([0-9a-fA-F]+)"')
HREF_RX = re.compile(r'<a\b[^>]*href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', re.I | re.S)
CONTACT_HINT = re.compile(r"contact|about|find.?us|enquir|get.?in.?touch|reserv|book|private|event|visit|location", re.I)

CLOSED_RX = re.compile(
    r"permanently closed|closed permanently|closed for good|now closed for good|has now closed|have now closed"
    r"|closed (?:its|our) doors (?:for the (?:last|final) time|for good|permanently)|ceased trading"
    r"|no longer trading|closed down|we are now closed\b(?! (?:on|for|until|today|tomorrow|this))", re.I)
PARKED_RX = re.compile(r"domain (?:is |may be )?for sale|buy this domain|this domain has expired|domain parking"
                       r"|parked free|is parked|hugedomains|sedo\.com|dan\.com/buy", re.I)

JUNK_DOMAINS = ("example.", "sentry", "wixpress", "domain.com", "email.com", "yourdomain",
                "godaddy", "squarespace.com", "mysite", "sentry-next", "@2x", "wix.com")
JUNK_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js")

# Websites that are not the venue's own site; no point crawling them for an email.
AGGREGATORS = ("facebook.com", "instagram.com", "twitter.com", "x.com", "tiktok.com", "deliveroo.",
               "ubereats.", "just-eat.", "justeat.", "opentable.", "tripadvisor.", "yelp.", "google.",
               "linktr.ee", "timeout.com", "squaremeal.", "designmynight.", "resdiary.", "sevenrooms.",
               "wetherspoon", "greeneking.", "stonegategroup", "foodhub.", "hungryhouse.")


def decode_cfemail(hexstr):
    """Decode a Cloudflare-obfuscated email (data-cfemail attribute)."""
    key = int(hexstr[:2], 16)
    return "".join(chr(int(hexstr[i:i + 2], 16) ^ key) for i in range(2, len(hexstr), 2))


def clean(email):
    email = urllib.parse.unquote(html.unescape(email)).strip().strip(".").lower()
    if not EMAIL_RX.fullmatch(email):
        return None
    if email.endswith(JUNK_SUFFIX) or any(j in email for j in JUNK_DOMAINS):
        return None
    return email


def extract_emails(page):
    """Return emails found in an HTML page, mailto links first."""
    found = []
    candidates = (MAILTO_RX.findall(page)
                  + [decode_cfemail(h) for h in CFEMAIL_RX.findall(page)]
                  + EMAIL_RX.findall(html.unescape(page).replace("[at]", "@").replace("(at)", "@")))
    for raw in candidates:
        e = clean(raw)
        if e and e not in found:
            found.append(e)
    return found


def contact_links(page, base_url, limit=4):
    """Same-site links that look like contact/about/booking pages."""
    host = urllib.parse.urlparse(base_url).netloc.lower().removeprefix("www.")
    links = []
    for href, text in HREF_RX.findall(page):
        if not (CONTACT_HINT.search(href) or CONTACT_HINT.search(re.sub("<[^>]+>", "", text))):
            continue
        url = urllib.parse.urljoin(base_url, html.unescape(href))
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme in ("http", "https") and parsed.netloc.lower().removeprefix("www.") == host \
                and url not in links and url.rstrip("/") != base_url.rstrip("/"):
            links.append(url)
    return links[:limit]


def normalize_url(url):
    url = url.strip()
    if not url:
        return ""
    if not re.match(r"https?://", url, re.I):
        url = "https://" + url.lstrip("/")
    return url


def is_aggregator(url):
    host = urllib.parse.urlparse(url).netloc.lower()
    return any(a in host for a in AGGREGATORS)


def best_email(emails, website):
    """Prefer an address on the venue's own domain, then any info@/hello@/bookings@ style address."""
    if not emails:
        return ""
    host = urllib.parse.urlparse(website).netloc.lower().removeprefix("www.")
    root = ".".join(host.split(".")[-3:]) if host.endswith(".co.uk") else ".".join(host.split(".")[-2:])
    own = [e for e in emails if e.split("@")[1].endswith(root)] if root else []
    pool = own or emails
    generic = [e for e in pool if re.match(r"(info|hello|contact|enquir|book|reserv|events|office|bookings)", e)]
    return (generic or pool)[0]


def _robots_ok(url, cache):
    parsed = urllib.parse.urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    if base not in cache:
        rp = urllib.robotparser.RobotFileParser()
        try:
            _, text = http.get(base + "/robots.txt", timeout=8)
            rp.parse(text.splitlines())
        except Exception:
            rp.parse([])
        cache[base] = rp
    return cache[base].can_fetch(http.USER_AGENT, url)


def visible_text(page):
    page = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", page)
    return html.unescape(re.sub(r"<[^>]+>", " ", page))


def site_status(page):
    """'closed' if the homepage says the venue shut down, 'parked' for a parked/for-sale domain, else 'ok'."""
    text = visible_text(page)
    if PARKED_RX.search(text) and len(text.split()) < 400:
        return "parked"
    if CLOSED_RX.search(text):
        return "closed"
    return "ok"


def fetch_status(exc):
    """Classify a fetch failure: 'dead' only when the site clearly no longer exists."""
    if isinstance(exc, urllib.error.HTTPError):
        return "dead" if exc.code in (404, 410) else "unknown"
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, socket.gaierror) or "Name or service not known" in str(reason) \
            or "nodename nor servname" in str(reason):
        return "dead"
    return "unknown"


def find_email(website):
    """Crawl a venue homepage plus a few contact-like pages.

    Returns (email, all_emails, status) where status is ok / closed / parked / dead / unknown / skipped.
    """
    website = normalize_url(website)
    if not website or is_aggregator(website):
        return "", [], "skipped"
    robots = {}
    emails = []
    try:
        if not _robots_ok(website, robots):
            return "", [], "unknown"
        final_url, page = http.get(website)
    except Exception as e:
        return "", [], fetch_status(e)
    status = site_status(page)
    if status != "ok":
        return "", [], status
    emails += extract_emails(page)
    if not emails:
        for link in contact_links(page, final_url):
            try:
                if _robots_ok(link, robots):
                    emails += [e for e in extract_emails(http.get(link)[1]) if e not in emails]
            except Exception:
                continue
            if emails:
                break
    return best_email(emails, final_url), emails, "ok"
