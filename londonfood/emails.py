"""Find contact email addresses on a venue's own website."""

import html
import re
import socket
import urllib.error
import urllib.parse
import urllib.robotparser

from . import http

EMAIL_RX = re.compile(r"[A-Za-z0-9._%+'-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){0,4}\.[A-Za-z]{2,24}")
DATA_URI = re.compile(r"data:[a-z/+.-]{1,40};base64,[A-Za-z0-9+/=]{100,}", re.I)
MAILTO_RX = re.compile(r"mailto:([^\"'?>\s]+)", re.I)
CFEMAIL_RX = re.compile(r'data-cfemail="([0-9a-fA-F]+)"')
HREF_RX = re.compile(r'<a\b[^>]*href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', re.I | re.S)
CONTACT_HINT = re.compile(r"contact|about|find.?us|enquir|get.?in.?touch|reserv|book|private|event|visit|location", re.I)

CLOSED_RX = re.compile(
    r"permanently closed|closed permanently|closed for good|now closed for good|has now closed|have now closed"
    r"|closed (?:its|our) doors (?:for the (?:last|final) time|for good|permanently)|ceased trading"
    r"|no longer trading|closed down|we are now closed\b(?! (?:on|for|until|today|tomorrow|this))"
    r"|ปิดกิจการ|ปิดถาวร|ปิดให้บริการถาวร", re.I)
PARKED_RX = re.compile(r"domain (?:is |may be )?for sale|buy this domain|this domain has expired|domain parking"
                       r"|parked free|is parked|hugedomains|sedo\.com|dan\.com/buy", re.I)

JUNK_DOMAINS = ("example.", "example@", "yourname@", "name@", "email@", "user@", "sentry", "wixpress", "domain.com", "email.com", "yourdomain",
                "godaddy", "squarespace.com", "mysite", "sentry-next", "@2x", "wix.com")
# Addresses that are useless for contacting the venue
NO_OUTREACH = re.compile(r"(privacy|gdpr|dpo|data|legal|investor|ir@|careers|jobs|recruit|hr@|noreply|no-reply"
                         r"|donotreply|webmaster|abuse|unsubscribe|accounts|invoice|payable|complaints|safeguarding)")
LOW_PRIORITY = ("press", "media", "pr@", "marketing", "partnerships", "partner", "customer", "feedback", "support")
JUNK_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js")

# Websites that are not the venue's own site; no point crawling them for an email.
# Not the venue's own website: social media, delivery/booking/review platforms, map links.
NOT_VENUE_LABELS = {"facebook", "fb", "instagram", "tiktok", "twitter", "linktr", "deliveroo", "ubereats",
                    "just-eat", "justeat", "opentable", "tripadvisor", "yelp", "timeout", "squaremeal",
                    "designmynight", "resdiary", "sevenrooms", "foodhub", "hungryhouse", "wolt", "youtube",
                    "threads", "snapchat", "beacons", "linkin", "whatsapp", "wa"}
NOT_VENUE_HOSTS = {"x.com", "google.com", "google.co.uk", "maps.google.com", "maps.google.co.uk", "goo.gl",
                   "maps.app.goo.gl", "g.page", "g.co", "bit.ly"}
# The venue's page on its pub company's site is its real website, but not worth crawling for an email.
NO_CRAWL_LABELS = {"wetherspoon", "jdwetherspoon", "greeneking", "stonegategroup"}


def _host(url):
    return urllib.parse.urlparse(normalize_url(url)).netloc.lower().split(":")[0].removeprefix("www.")


def not_venue_site(url):
    """True for social media / delivery / booking / review / map links rather than the venue's own site."""
    host = _host(url)
    if host in NOT_VENUE_HOSTS or host.endswith(".x.com"):
        return True
    return bool(set(host.split(".")) & NOT_VENUE_LABELS)


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


_SP = r"[ \t]{0,3}"
OBF_AT = re.compile(_SP + r"(?:\[at\]|\(at\)|\{at\})" + _SP
                    + r"|[ \t]{1,3}at[ \t]{1,3}(?=[a-z0-9-]{1,63}[ \t]{0,3}(?:\[dot\]|\(dot\)|\{dot\}|[ \t]dot[ \t]))", re.I)
OBF_DOT = re.compile(_SP + r"(?:\[dot\]|\(dot\)|\{dot\})" + _SP + r"|[ \t]{1,3}dot[ \t]{1,3}", re.I)
FALLBACK_PATHS = ("/contact", "/contact-us", "/contactus", "/contact.html", "/about", "/about-us", "/find-us",
                  "/private-hire", "/events", "/info")


def deobfuscate(text):
    """'info [at] venue [dot] co [dot] uk' / 'info at venue dot com' -> info@venue.co.uk / info@venue.com."""
    return OBF_DOT.sub(".", OBF_AT.sub("@", text))


def extract_emails(page):
    """Return emails found in an HTML page, mailto links first."""
    page = DATA_URI.sub(" ", page[:2_000_000])
    found = []
    candidates = (MAILTO_RX.findall(page)
                  + [decode_cfemail(h) for h in CFEMAIL_RX.findall(page)]
                  + EMAIL_RX.findall(deobfuscate(html.unescape(page))))
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
    """Links not worth crawling for an email: other platforms, and pub-company venue pages."""
    return not_venue_site(url) or bool(set(_host(url).split(".")) & NO_CRAWL_LABELS)


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


SOFT_REDIRECT_RX = re.compile(
    r"""http-equiv=["']?refresh["']?[^>]*url=['"]?([^"'>\s]+)|(?:window|document)\.location(?:\.href)?\s*=\s*["']([^"']+)""",
    re.I)


def soft_redirect(page):
    """Target of a meta-refresh / JS redirect on a near-empty page, else None."""
    if len(page) > 3000:
        return None
    m = SOFT_REDIRECT_RX.search(page)
    return (m.group(1) or m.group(2)) if m else None


def site_status(page):
    """'closed' if the homepage says the venue shut down, 'parked' for a parked/for-sale domain, else 'ok'."""
    text = visible_text(page)
    if PARKED_RX.search(text) and len(text.split()) < 400:
        return "parked"
    if CLOSED_RX.search(text):
        return "closed"
    return "ok"


def fetch_status(exc, url=None):
    """Classify a fetch failure: 'dead' only when the site clearly no longer exists."""
    if isinstance(exc, urllib.error.HTTPError):
        return "dead" if exc.code in (404, 410) else "unknown"
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, socket.gaierror) or "Name or service not known" in str(reason) \
            or "nodename nor servname" in str(reason):
        return "dead"
    if url and "Tunnel connection failed" in str(reason):  # proxy can't connect: is the domain gone?
        if http.domain_exists(urllib.parse.urlparse(url).netloc.split(":")[0]) is False:
            return "dead"
    return "unknown"


THAI_RX = re.compile(r"[\u0E00-\u0E7F]")
LATIN_RX = re.compile(r"[A-Za-z]")
HTML_LANG_RX = re.compile(r"<html[^>]{0,300}?\blang=[\"']?([a-zA-Z-]{2,10})", re.I)
EN_VERSION_RX = re.compile(r"""hreflang=["']?en|href=["'][^"']{0,200}(?:/en(?:-[a-z]{2})?/|[?&]lang=en)""", re.I)


def page_language(page):
    """'English', 'Bilingual' or 'Local' from the homepage: <html lang>, script mix, and any English version link."""
    text = visible_text(page[:1_000_000])
    thai, latin = len(THAI_RX.findall(text)), len(LATIN_RX.findall(text))
    ratio = latin / (latin + thai) if latin + thai else 0
    m = HTML_LANG_RX.search(page[:5000])
    declared = m.group(1).lower() if m else ""
    has_en_version = bool(EN_VERSION_RX.search(page[:1_000_000]))
    if ratio >= 0.85 or (declared.startswith("en") and ratio >= 0.6):
        return "English"
    if ratio >= 0.3 or has_en_version:
        return "Bilingual"
    return "Local"


def find_email(website, info=None):
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
        return "", [], fetch_status(e, website), website
    target = soft_redirect(page)
    if target:  # tiny page that only redirects via meta refresh / JavaScript
        if re.search(r"/lander\b", target):
            return "", [], "parked", final_url
        try:
            final_url, page = http.get(urllib.parse.urljoin(final_url, target))
        except Exception:
            pass
    status = site_status(page)
    if status != "ok":
        return "", [], status, final_url
    if info is not None:
        info["lang"] = page_language(page)
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
    if not best_email(emails, [website, final_url]):
        # menus built by JavaScript hide contact links: try the usual contact pages on the venue's own site
        root = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(final_url))
        for path in FALLBACK_PATHS:
            try:
                if _robots_ok(root + path, robots):
                    emails += [e for e in extract_emails(http.get(root + path, timeout=10)[1]) if e not in emails]
            except Exception:
                continue
            if best_email(emails, [website, final_url]):
                break
    return best_email(emails, [website, final_url]), emails, "ok", final_url
