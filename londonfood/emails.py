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
# Addresses that are useless for contacting the venue
NO_OUTREACH = re.compile(r"(privacy|gdpr|dpo|data|legal|investor|ir@|careers|jobs|recruit|hr@|noreply|no-reply"
                         r"|donotreply|webmaster|abuse|unsubscribe|accounts|invoice|payable|complaints|safeguarding)")
LOW_PRIORITY = ("press", "media", "pr@", "marketing", "partnerships", "partner", "customer", "feedback", "support")
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
    email = re.sub(r"^(?:u003[ce]|x3[ce]|u0022|20)+", "", email)  # JSON / URL escape debris
    if not EMAIL_RX.fullmatch(email):
        return None
    if email.endswith(JUNK_SUFFIX) or any(j in email for j in JUNK_DOMAINS) or NO_OUTREACH.match(email):
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


FREEMAIL = ("gmail.com", "googlemail.com", "hotmail.com", "hotmail.co.uk", "outlook.com", "live.com",
            "live.co.uk", "yahoo.com", "yahoo.co.uk", "icloud.com", "me.com", "btinternet.com",
            "aol.com", "protonmail.com", "proton.me", "sky.com", "virginmedia.com")
ROLE_RANK = ["reservations", "bookings", "booking", "book", "reserve", "info", "hello", "contact", "enquir",
             "events", "office", "manager", "admin", "team", "sales", "orders", "catering"]


def site_root(url):
    host = urllib.parse.urlparse(url).netloc.lower().split(":")[0].removeprefix("www.")
    parts = host.split(".")
    return ".".join(parts[-3:]) if len(parts) > 2 and parts[-2] in ("co", "org", "ac", "com") else ".".join(parts[-2:])


def valid_emails(emails, website):
    """Emails on the venue's own domain (or free-mail), best first: role addresses (info@, bookings@) lead.

    Addresses on other companies' domains (web agencies, landlords, parent groups) are dropped.
    Nothing is ever guessed: every address returned was published on the venue's site.
    """
    emails = list(dict.fromkeys(c for c in (clean(e) for e in emails) if c))
    roots = {site_root(u) for u in (website if isinstance(website, (list, tuple)) else [website]) if u}
    ok = [e for e in emails if any(e.split("@")[1] == r or e.split("@")[1].endswith("." + r) for r in roots)
          or e.split("@")[1] in FREEMAIL]

    def rank(e):
        local = e.split("@")[0]
        for i, role in enumerate(ROLE_RANK):
            if local.startswith(role):
                return (0, i)
        if e.startswith(LOW_PRIORITY):
            return (3, 0)
        return (1 if "." not in local else 2, 0)  # personal first.last addresses after role ones

    return sorted(ok, key=rank)


def best_email(emails, website):
    found = valid_emails(emails, website)
    return found[0] if found else ""


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

    Returns (email, all_emails, status, final_url); status is ok / closed / parked / dead / unknown / skipped.
    """
    website = normalize_url(website)
    if not website or is_aggregator(website):
        return "", [], "skipped", website
    robots = {}
    emails = []
    try:
        if not _robots_ok(website, robots):
            return "", [], "unknown", website
        final_url, page = http.get(website)
    except Exception as e:
        return "", [], fetch_status(e), website
    status = site_status(page)
    if status != "ok":
        return "", [], status, final_url
    emails += extract_emails(page)
    if not best_email(emails, [website, final_url]):
        for link in contact_links(page, final_url):
            try:
                if _robots_ok(link, robots):
                    emails += [e for e in extract_emails(http.get(link)[1]) if e not in emails]
            except Exception:
                continue
            if best_email(emails, [website, final_url]):
                break
    return best_email(emails, [website, final_url]), emails, "ok", final_url
