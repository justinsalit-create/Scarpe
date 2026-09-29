"""Facebook / Instagram profile links for venues with no email and no website (client rule).

Only the venue's own Facebook page or Instagram account, as tagged on the map (or given as its
"website"), normalised to a canonical https profile URL. TikTok and other platforms are never used.
Whether the account is still active can't be checked from here (no access to Meta's sites).
"""

import re
import urllib.parse

IG_RESERVED = {"p", "reel", "reels", "explore", "stories", "tv", "accounts", "direct", "about", "developer"}
FB_RESERVED = {"sharer", "sharer.php", "share", "dialog", "login", "events", "groups", "photo", "photo.php",
               "watch", "marketplace", "hashtag", "search", "help", "policies", "l.php", "plugins"}


def canonical_instagram(value):
    v = (value or "").strip()
    if not v:
        return ""
    if "instagram.com" not in v.lower() and "/" not in v:
        user = v.lstrip("@")
    else:
        path = urllib.parse.urlparse(v if "://" in v else "https://" + v).path
        user = (path.strip("/").split("/") or [""])[0]
    user = user.strip().lower()
    if not re.fullmatch(r"[a-z0-9._]{1,30}", user) or user in IG_RESERVED:
        return ""
    return f"https://www.instagram.com/{user}/"


def canonical_facebook(value):
    v = (value or "").strip()
    if not v:
        return ""
    if not re.search(r"facebook\.com|fb\.com|fb\.me", v, re.I) and "/" not in v:
        return f"https://www.facebook.com/{v.lstrip('@')}" if re.fullmatch(r"[A-Za-z0-9.\-]{2,80}", v.lstrip("@")) else ""
    u = urllib.parse.urlparse(v if "://" in v else "https://" + v)
    parts = [p for p in u.path.split("/") if p]
    if parts and parts[0].lower() == "profile.php":
        pid = urllib.parse.parse_qs(u.query).get("id", [""])[0]
        return f"https://www.facebook.com/profile.php?id={pid}" if pid.isdigit() else ""
    if parts and parts[0].lower() == "pages" and len(parts) >= 2:
        parts = parts[1:]
        # /pages/Name/123456 -> the numeric id is the stable handle
        if len(parts) >= 2 and parts[1].isdigit():
            return f"https://www.facebook.com/{parts[1]}"
    if parts and parts[0].lower() == "p" and len(parts) >= 2:
        parts = parts[1:]
    if not parts or parts[0].lower() in FB_RESERVED:
        return ""
    return f"https://www.facebook.com/{parts[0]}"


def collect(venue):
    """Fill venue['facebook'] / venue['instagram'] with canonical URLs from the map tags."""
    t = venue.get("tags", {})
    fb = [t.get(k) for k in ("contact:facebook", "facebook")] + [venue.get("facebook")]
    ig = [t.get(k) for k in ("contact:instagram", "instagram")] + [venue.get("instagram")]
    site = venue.get("social_website", "")
    if re.search(r"facebook\.com|fb\.com|fb\.me", site, re.I):
        fb.append(site)
    if "instagram.com" in site.lower():
        ig.append(site)
    venue["facebook"] = next((c for c in map(canonical_facebook, fb) if c), "")
    venue["instagram"] = next((c for c in map(canonical_instagram, ig) if c), "")
    venue["social_url"] = venue["instagram"] or venue["facebook"]
