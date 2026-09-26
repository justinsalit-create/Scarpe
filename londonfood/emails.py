"""Find contact email addresses on a venue's own website."""

import html
import re
import urllib.parse
import urllib.robotparser

from . import http

EMAIL_RX = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}")
MAILTO_RX = re.compile(r"mailto:([^\"'?>\s]+)", re.I)
CFEMAIL_RX = re.compile(r'data-cfemail="([0-9a-fA-F]+)"')
HREF_RX = re.compile(r'<a\b[^>]*href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', re.I | re.S)
CONTACT_HINT = re.compile(r"contact|about|find.?us|enquir|get.?in.?touch|reserv|book|private|event|visit|location", re.I)

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


def find_email(website):
    """Crawl a venue homepage plus a few contact-like pages. Returns (email, all_emails)."""
    website = normalize_url(website)
    if not website or is_aggregator(website):
        return "", []
    robots = {}
    emails = []
    try:
        if not _robots_ok(website, robots):
            return "", []
        final_url, page = http.get(website)
    except Exception:
        return "", []
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
    return best_email(emails, final_url), emails
